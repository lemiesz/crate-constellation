"""USB stick (rekordbox export) reader + diff vs library.db.

Read side is zero-risk: the stick's exportLibrary.db (OneLibrary, SQLCipher
with the public static key) is copied to a temp dir and opened there — the
stick itself is never written by the explorer. Its content.masterContentId
maps 1:1 to library.db track ids (= rekordbox ContentIDs).

The only write action exposed is the deck-verified matching patch, executed
via the proven scripts in ~/Music/library-management (backup-first, gated).
"""

import os
import shutil
import sqlite3
import tempfile
from pathlib import Path

import sqlcipher3

DLP_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"
# never treat source/system volumes as "the stick" (T7 carries a stale
# ancient device export that otherwise shadows real sticks)
EXCLUDE_VOLUMES = {"Macintosh HD", "com.apple.TimeMachine.localsnapshots",
                   "T7", "drcomputer-backup"}

_cache: dict = {}


def pioneer_dir(vol: Path) -> Path | None:
    """The library dir: 'PIONEER' on FAT, hidden '.PIONEER' on HFS+."""
    for name in (".PIONEER", "PIONEER"):
        if (vol / name / "rekordbox").exists():
            return vol / name
    return None


def find_stick(volume: str | None = None) -> Path | None:
    """The mounted rekordbox-exported stick; `volume` pins a specific one."""
    vols = Path("/Volumes")
    if not vols.exists():
        return None
    for v in sorted(vols.iterdir()):
        if volume and v.name != volume:
            continue
        if not volume and (v.name in EXCLUDE_VOLUMES or v.name.startswith(".")):
            continue
        pd = pioneer_dir(v)
        if pd and (pd / "rekordbox/exportLibrary.db").exists():
            return v
    return None


def _read_stick_db(vol: Path) -> dict:
    """Copy exportLibrary.db (+wal/shm) off-stick and read the content table."""
    src = pioneer_dir(vol) / "rekordbox/exportLibrary.db"
    with tempfile.TemporaryDirectory() as td:
        for suf in ("", "-wal", "-shm"):
            f = Path(str(src) + suf)
            if f.exists():
                shutil.copy2(f, Path(td) / f.name)
        db = sqlcipher3.connect(str(Path(td) / "exportLibrary.db"))
        db.execute(f"PRAGMA key = '{DLP_KEY}'")
        rows = db.execute(
            "SELECT content_id, masterContentId, title, bpmx100, length, fileSize, fileName "
            "FROM content WHERE masterContentId IS NOT NULL").fetchall()
        pairs = db.execute("SELECT COUNT(*) FROM recommendedLike").fetchone()[0]
        playlists = db.execute(
            "SELECT playlist_id, name, playlist_id_parent, attribute, sequenceNo "
            "FROM playlist").fetchall()
        entries = db.execute(
            "SELECT playlist_id, content_id, sequenceNo FROM playlist_content "
            "ORDER BY playlist_id, sequenceNo").fetchall()
        db.close()
    return {"rows": rows, "pairs": pairs, "playlists": playlists, "entries": entries}


def scan(vol: Path, library_db: Path) -> dict:
    """Diff the stick against the app library. Cached per stick-db mtime."""
    src = pioneer_dir(vol) / "rekordbox/exportLibrary.db"
    key = (str(vol), src.stat().st_mtime_ns)
    if _cache.get("key") == key:
        return _cache["result"]

    stick = _read_stick_db(vol)
    on_stick = {}  # master id -> stick row
    for cid, mid, title, bpmx100, length, size, fname in stick["rows"]:
        on_stick[str(mid)] = {"content_id": cid, "title": title,
                              "bpm": (bpmx100 or 0) / 100 or None,
                              "length": length, "size": size, "file": fname}

    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    lib = {r["id"]: dict(r) for r in con.execute(
        "SELECT t.id, t.title, t.artist, t.bpm, t.energy, t.length, f.path "
        "FROM tracks t LEFT JOIN files f ON f.track_id = t.id")}
    con.close()

    synced, missing, missing_bytes = [], [], 0
    for tid, r in lib.items():
        if tid in on_stick:
            r2 = dict(r)
            r2["status"] = "synced"
            r2["size"] = on_stick[tid]["size"]
            synced.append(r2)
        else:
            r2 = dict(r)
            r2["status"] = "missing"
            try:
                r2["size"] = os.stat(r["path"]).st_size if r["path"] else None
            except OSError:
                r2["size"] = None
            missing_bytes += r2["size"] or 0
            missing.append(r2)
    stick_only = [{"id": None, "title": v["title"], "artist": None,
                   "bpm": v["bpm"], "energy": None, "length": v["length"],
                   "path": v["file"], "size": v["size"], "status": "stick_only"}
                  for mid, v in on_stick.items() if mid not in lib]

    # playlist tree + per-playlist track order (content_id -> master id)
    cid_to_mid = {v["content_id"]: mid for mid, v in on_stick.items()}
    pl_tracks: dict[str, list] = {}
    for pid, cid, _seq in stick["entries"]:
        pl_tracks.setdefault(str(pid), []).append(cid_to_mid.get(cid, f"stick:{cid}"))
    playlists = [{"id": str(pid), "name": name,
                  "parent_id": None if parent == 0 else str(parent),
                  "kind": "folder" if attr == 1 else "playlist", "seq": seq,
                  "n": len(pl_tracks.get(str(pid), []))}
                 for pid, name, parent, attr, seq in stick["playlists"]]

    by_id = {r["id"]: r for r in synced}
    by_id.update({r["id"]: r for r in missing})
    stick_only_by_cid = {}
    for mid, v in on_stick.items():
        if mid not in lib:
            stick_only_by_cid[f"stick:{v['content_id']}"] = {
                "id": None, "title": v["title"], "artist": None, "bpm": v["bpm"],
                "energy": None, "length": v["length"], "path": v["file"],
                "size": v["size"], "status": "stick_only"}

    st = os.statvfs(vol)
    result = {
        "volume": vol.name,
        "free_bytes": st.f_bavail * st.f_frsize,
        "total_bytes": st.f_blocks * st.f_frsize,
        "on_stick": len(on_stick),
        "synced": len(synced),
        "missing": len(missing),
        "stick_only": len(stick_only),
        "missing_bytes": missing_bytes,
        "matching_pairs": stick["pairs"],
        "tracks": {"synced": synced, "missing": missing, "stick_only": stick_only},
        "playlists": playlists,
        "playlist_tracks": pl_tracks,
        "by_id": by_id,
        "stick_only_by_cid": stick_only_by_cid,
    }
    _cache.update(key=key, result=result)
    return result
