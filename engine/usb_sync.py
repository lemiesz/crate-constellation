"""USB sync wizard engine — build a playable stick from a cleared USB.

What it writes today (from scratch, no rekordbox):
  - audio files      -> /Contents/<Artist>/<file>
  - analysis files   -> /PIONEER/USBANLZ/... (copied from the local share,
                        PPTH rewritten to the stick path)
  - exportLibrary.db -> OneLibrary built from the preserved corpus template:
                        content/artist/album/key/label/playlist/myTag tables
                        (playable on CDJ-3000/AZ/OPUS; importable by rekordbox)
  - player settings  -> DEVSETTING/MYSETTING from the corpus

What it does NOT write yet: export.pdb / exportExt.pdb — the legacy database
the XDJ-XZ reads. That writer is the gated milestone; until it lands the XZ
sees this stick in folder-browse mode only.
"""

import os
import re
import shutil
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
import time
import unicodedata
from datetime import date
from pathlib import Path

import sqlcipher3

HERE = Path(__file__).resolve().parent
CORPUS = HERE.parent / "pdb-corpus"
RB_SHARE = Path.home() / "Library/Pioneer/rekordbox/share"
DLP_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"
from .usb import EXCLUDE_VOLUMES
FILETYPE = {".mp3": 1, ".m4a": 4, ".aac": 4, ".flac": 5, ".wav": 11, ".aiff": 12, ".aif": 12}

# Bookkeeping constants rekordbox stamps on every exported track.  Both are
# copied from a rekordbox-written export (pdb-corpus/fresh-export); leaving
# them NULL is one of the deviations that made rekordbox call our device
# libraries corrupt.  analysedBits = which analysis data exists (41 is what
# rekordbox writes for a normally analysed track); contentLink is a constant
# stamp, 788224 across every fresh export we have.
ANALYSED_BITS = 41
CONTENT_LINK = 788224
# The rekordbox library id. Appears in three places that must agree: the u32
# at track row offset 0x18 in export.pdb, content.masterDbId in
# exportLibrary.db, and DBID in the sync ledgers.
MASTER_DB_ID = 2090934051


def art_bucket(artwork_id: int) -> str:
    """Artwork directory for an id: rekordbox shards 20 covers per bucket.

    Verified against a rekordbox-written export: 11,485 artwork rows, ids 1
    to 11,485, buckets 00001 to 00575, zero violations.  Every third-party
    writer flattens everything into 00001, which is right only while a stick
    holds fewer than twenty distinct covers."""
    return f"{artwork_id // 20 + 1:05d}"


def audio_props(path: str | None) -> tuple[int, int, int]:
    """(bitrate kbps, bit depth, sample rate) read from the file itself.
    rekordbox always fills these; we used to write zeros."""
    if not path:
        return 0, 16, 44100
    try:
        import mutagen
        f = mutagen.File(path)
        info = getattr(f, "info", None)
        if info is None:
            return 0, 16, 44100
        br = int(getattr(info, "bitrate", 0) or 0) // 1000
        depth = int(getattr(info, "bits_per_sample", 0) or 16)
        srate = int(getattr(info, "sample_rate", 0) or 44100)
        return br, depth, srate
    except Exception:
        return 0, 16, 44100

JOBS: dict[str, dict] = {}          # volume -> job (one live job per drive)


def jobs_snapshot() -> list[dict]:
    out = []
    for vol, j in JOBS.items():
        out.append({k: v for k, v in j.items()
                    if k not in ("_library_db", "abort")})
    out.sort(key=lambda j: -(j.get("started") or 0))
    return out


def _persist_log(job: dict, library_db: Path) -> None:
    """Append the finished job to sync_log in library.db (the debug history)."""
    try:
        con = sqlite3.connect(library_db)
        con.execute(
            "CREATE TABLE IF NOT EXISTS sync_log ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT, finished_at TEXT,"
            "volume TEXT, state TEXT, n_tracks INTEGER, n_playlists INTEGER,"
            "bytes INTEGER, duration_s REAL, mb_s REAL, error TEXT, log TEXT)")
        import json
        from datetime import datetime, timezone
        dur = time.time() - job.get("started", time.time())
        by = job.get("bytes_done", 0)
        con.execute(
            "INSERT INTO sync_log (started_at, finished_at, volume, state, n_tracks,"
            " n_playlists, bytes, duration_s, mb_s, error, log) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (datetime.fromtimestamp(job.get("started", time.time()), tz=timezone.utc)
             .strftime("%Y-%m-%d %H:%M:%S"),
             datetime.now(tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
             job.get("volume"), job.get("state"), job.get("total"),
             job.get("n_playlists"), by, round(dur, 1),
             round(by / 1e6 / max(dur, 0.01), 2), job.get("error"),
             json.dumps(job.get("log", []))[-4000:]))
        con.commit()
        con.close()
    except Exception:
        pass                                     # logging must never kill a sync


def kill(volume: str) -> dict:
    """Force-release that drive's running/stale job: the worker self-aborts at
    its next checkpoint; the per-drive lock frees immediately."""
    job = JOBS.get(volume)
    if not job or job.get("state") != "running":
        return {"killed": False, "state": job.get("state") if job else "none"}
    job["abort"] = True
    job.update(state="killed", error="killed from debug view")
    if job.get("_library_db"):
        _persist_log(job, Path(job["_library_db"]))
    return {"killed": True}


def cancel(volume: str) -> dict:
    job = JOBS.get(volume)
    if job:
        job["cancel"] = True
    return {"ok": bool(job)}


def dismiss(volume: str) -> dict:
    """Drop a finished (done/error/cancelled/killed) job from the drawer.
    Running jobs must be cancelled or killed first — never silently dropped."""
    job = JOBS.get(volume)
    if not job:
        return {"dismissed": False, "state": "none"}
    if job.get("state") == "running":
        return {"dismissed": False, "state": "running"}
    del JOBS[volume]
    return {"dismissed": True}


def manifest_path(vol: Path) -> Path:
    """Our own sync manifest, one level ABOVE the rekordbox directory.

    That directory must contain rekordbox's own files and nothing else — a
    foreign file sitting next to export.pdb is exactly the kind of thing
    rekordbox's device scan trips over.  Older sticks kept it inside; the
    reader still accepts that location."""
    return vol / target_pioneer_name(vol) / "crate-sync.json"


def read_manifest(vol: Path) -> dict | None:
    """The crate-sync.json a sync leaves on the stick (what/when we synced)."""
    import json
    pname = target_pioneer_name(vol)
    for f in (manifest_path(vol), vol / pname / "rekordbox" / "crate-sync.json"):
        if f.exists():
            try:
                return json.loads(f.read_text())
            except Exception:
                return None
    return None


def extract_art(path: str) -> bytes | None:
    """Embedded cover art (JPEG only — what decks render) via mutagen."""
    try:
        import mutagen
        from mutagen.id3 import ID3
        from mutagen.mp4 import MP4
        from mutagen.flac import FLAC
        low = path.lower()
        if low.endswith(".mp3") or low.endswith(".aiff") or low.endswith(".aif"):
            tags = ID3(path)
            for k, v in tags.items():
                if k.startswith("APIC") and v.mime in ("image/jpeg", "image/jpg"):
                    return bytes(v.data)
        elif low.endswith(".m4a") or low.endswith(".mp4") or low.endswith(".aac"):
            covr = MP4(path).tags.get("covr") if MP4(path).tags else None
            if covr and int(covr[0].imageformat) == 13:      # 13 = JPEG
                return bytes(covr[0])
        elif low.endswith(".flac"):
            for pic in FLAC(path).pictures:
                if pic.mime in ("image/jpeg", "image/jpg"):
                    return bytes(pic.data)
    except Exception:
        pass
    return None


def patch_anlz_path(raw: bytes, new_path: str) -> bytes:
    """Byte-splice a new PPTH (UTF-16-BE, NULL-TERMINATED — deck rejects
    otherwise) into an ANLZ file and fix the PMAI total-length field."""
    import struct as _s
    i = raw.find(b"PPTH")
    if i < 0:
        return raw
    old_tl = _s.unpack(">I", raw[i + 8:i + 12])[0]
    pb = (new_path + "\x00").encode("utf-16-be")
    sect = b"PPTH" + _s.pack(">III", 16, 16 + len(pb), len(pb)) + pb
    out = bytearray(raw[:i] + sect + raw[i + old_tl:])
    _s.pack_into(">I", out, 8, len(out))          # PMAI total file length
    return bytes(out)


def anlz_hash(file_path: str) -> tuple[int, int]:
    """Pioneer's ANLZ directory hash (disassembled from rekordbox; players
    IGNORE the pdb analyze_path and recompute this from the audio path).
    Validated 4/4 against a deck-verified export."""
    h = 0
    for ch in file_path:
        c = ord(ch) & 0xFFFF
        t = (h * 0x5BC9 + c) & 0xFFFFFFFF
        h = (t * 0x93B5 + c) & 0xFFFFFFFF
    hr = h % 200003
    pv = ((hr >> 0) & 0x01) | ((hr >> 1) & 0x02) | ((hr >> 4) & 0x04) |          ((hr >> 4) & 0x08) | ((hr >> 5) & 0x10) | ((hr >> 8) & 0x20) | ((hr >> 10) & 0x40)
    return pv, hr
FRESH = CORPUS / "fresh-export"

# The player settings files.  Decks read these AND write them back, so a
# device is expected to carry all five; a drive missing them is one rekordbox
# has never prepared.  Each is taken from the most recent genuine export that
# has it — they are byte-identical across our sources except DEVSETTING.DAT,
# which only the newest one carries.
SETTINGS_NAMES = ("DEVSETTING.DAT", "MYSETTING.DAT", "MYSETTING2.DAT",
                  "DJMMYSETTING.DAT", "djprofile.nxs")
SETTINGS_SOURCES = (CORPUS / "rb-export-2026-09-07/.PIONEER", FRESH, CORPUS)


# EXPERIMENTAL — drives that already hold a mirror of the library.
#
# A backup drive carries the same audio the library points at, so copying it
# again wastes both time and a second copy of every byte. Where the mirror is
# on the SAME volume we can hard-link instead: the deck-facing file under
# /Contents becomes a second name for the file already on the drive, costing
# no space and reading as an ordinary file to anything that opens it.
#
# Hard links, not symlinks. A player reads the filesystem directly and cannot
# be relied on to follow a symlink, and a symlink also stores an absolute
# path that means nothing on the deck. A hard link is indistinguishable from
# a regular file.
#
# The two names share one set of bytes, so editing the audio through
# /Contents would edit the backup as well. We only ever read audio, and the
# UI says so on the drive this is enabled for.
#
#   mirror: directory on the volume under which the library tree is mirrored
#   strip:  prefix of the library path that the mirror replaces
LINK_MODE = {
    "drcomputer-backups": {"mirror": "latest", "strip": "/Volumes/T7/"},
}


def link_mode(volume: str) -> dict | None:
    return LINK_MODE.get(volume)


# Files above this size are copied in chunks so a cancel stays responsive;
# below it the kernel copy is used whole, which is much faster per file.
CHUNKED_ABOVE = 64 * 1024 * 1024
CHUNK = 8 * 1024 * 1024


# Progress used to be bytes copied over bytes total, so it reached 100% the
# moment the audio finished and then sat there through analysis, artwork and
# the databases. On a link-mode drive that is absurd: a 19,717-track sync
# spent 2.5s on audio and 328s after it, so the bar was full for 99% of the
# run and the job looked finished, then stale.
#
# Progress is now the weighted sum of every phase. The weights are estimates
# of how long each phase will take, measured on that same sync: analysis
# 10.2ms per track, artwork 1.6ms, databases 4.7ms, and copying at roughly
# 100 MB/s. Estimating rather than fixing the weights matters because audio
# dominates when copying and is nearly free when linking.
PHASE_MS = {"analysis": 0.0102, "artwork": 0.0016, "databases": 0.0047,
            # verification, settings, manifest and the flush: small, but
            # the bar must not read 100% while they are still running
            "finalize": 0.0012}
COPY_BYTES_PER_S = 100e6
# Analysis copying is I/O bound per track, so a small pool overlaps the waits.
ANLZ_WORKERS = 8


def phase_weights(n_tracks: int, bytes_to_copy: int) -> dict:
    est = {"audio": bytes_to_copy / COPY_BYTES_PER_S}
    est.update({k: n_tracks * v for k, v in PHASE_MS.items()})
    total = sum(est.values()) or 1.0
    return {k: v / total for k, v in est.items()}


def set_progress(job: dict, phase: str, fraction: float) -> None:
    """Record a phase as `fraction` complete and recompute the overall bar."""
    job.setdefault("phases", {})[phase] = min(1.0, max(0.0, fraction))
    w = job.get("weights") or {}
    job["progress"] = sum(w.get(k, 0.0) * v for k, v in job["phases"].items())


def copy_file(src: str, dst: Path, size: int, job: dict, alive) -> bool:
    """Copy one track as fast as the platform allows.

    shutil.copyfile uses macOS fcopyfile, a single kernel-side copy that
    beats reading and writing through Python buffers. We give up per-chunk
    progress for it, which only matters on files big enough that a cancel
    would feel stuck, so those still go through the chunked path.
    """
    if size and size <= CHUNKED_ABOVE:
        shutil.copyfile(src, dst)
        job["bytes_done"] += size
        return True
    with open(src, "rb") as fi, open(dst, "wb") as fo:
        while chunk := fi.read(CHUNK):
            if not alive():
                return False
            fo.write(chunk)
            job["bytes_done"] += len(chunk)
    return True


def link_source(vol: Path, track_path: str, size: int | None) -> Path | None:
    """Where this track already lives on the target volume, if it does."""
    cfg = LINK_MODE.get(vol.name)
    if not cfg or not track_path or not track_path.startswith(cfg["strip"]):
        return None
    cand = (vol / cfg["mirror"] / track_path[len(cfg["strip"]):]).resolve()
    try:
        if cand.is_file() and (size is None or cand.stat().st_size == size):
            return cand
    except OSError:
        pass
    return None


def settings_source(name: str) -> Path | None:
    return next((d / name for d in SETTINGS_SOURCES if (d / name).exists()), None)


def target_pioneer_name(vol: Path) -> str:
    """rekordbox hides the library dir dot-prefixed on HFS+; plain on FAT."""
    import subprocess
    r = subprocess.run(["diskutil", "info", str(vol)], capture_output=True, text=True)
    return "PIONEER" if "FAT" in r.stdout.upper() else ".PIONEER"


def targets() -> list[dict]:
    out = []
    vols = Path("/Volumes")
    if not vols.exists():
        return out
    for v in sorted(vols.iterdir()):
        if v.name in EXCLUDE_VOLUMES or v.name.startswith(".") or not v.is_dir():
            continue
        try:
            st = os.statvfs(v)
        except OSError:
            continue
        out.append({"volume": v.name,
                    "free_bytes": st.f_bavail * st.f_frsize,
                    "total_bytes": st.f_blocks * st.f_frsize,
                    "has_export": (v / "PIONEER/rekordbox/exportLibrary.db").exists()})
    return out


def _safe(name: str) -> str:
    return re.sub(r'[/\\:*?"<>|]', "_", name or "Unknown").strip() or "Unknown"


def plan(library_db: Path, playlist_ids: list[str]) -> dict:
    """Dedupe tracks across the chosen playlists, in tree order; size the copy."""
    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    tracks, seen = [], set()
    pls = []
    for pid in playlist_ids:
        p = con.execute("SELECT id, name FROM playlists WHERE id = ?", (pid,)).fetchone()
        if not p:
            continue
        rows = con.execute(
            "SELECT t.id, t.title, t.artist, t.album, t.label, t.bpm, t.key, t.length, "
            "t.rating, t.year, t.comment, f.path, f.anlz_path "
            "FROM playlist_tracks pt JOIN tracks t ON t.id = pt.track_id "
            "LEFT JOIN files f ON f.track_id = t.id "
            "WHERE pt.playlist_id = ? ORDER BY pt.seq", (pid,)).fetchall()
        entry_ids = []
        for r in rows:
            entry_ids.append(r["id"])
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            d = dict(r)
            try:
                d["size"] = os.stat(r["path"]).st_size if r["path"] else None
            except OSError:
                d["size"] = None
            tracks.append(d)
        pls.append({"id": p["id"], "name": p["name"], "track_ids": entry_ids})
    # nested tree: include ancestor folders of every selected playlist, in
    # library order, with stick-side ids assigned depth-first
    all_pls = {r["id"]: dict(r) for r in con.execute(
        "SELECT id, name, parent_id, kind, seq FROM playlists")}
    con.close()
    selected = {p2["id"] for p2 in pls}
    keep = set(selected)
    for pid in selected:                       # walk up to the roots
        cur = all_pls.get(pid, {}).get("parent_id")
        while cur:
            keep.add(cur)
            cur = all_pls.get(cur, {}).get("parent_id")
    nodes, sid_of = [], {}
    tracks_of = {p2["id"]: p2["track_ids"] for p2 in pls}

    def walk(parent_lib, parent_sid):
        kids = sorted((q for q in all_pls.values()
                       if (q["parent_id"] or None) == parent_lib and q["id"] in keep),
                      key=lambda q: (q["seq"] or 0, q["name"]))
        for sort, q in enumerate(kids):
            sid = len(nodes) + 1
            sid_of[q["id"]] = sid
            nodes.append({"sid": sid, "parent_sid": parent_sid, "sort": sort,
                          "is_folder": q["kind"] == "folder", "name": q["name"],
                          "lib_id": int(q["id"]),
                          "parent_lib_id": int(parent_lib) if parent_lib else 0,
                          "track_ids": tracks_of.get(q["id"], [])})
            if q["kind"] == "folder":
                walk(q["id"], sid)
    walk(None, 0)

    missing_files = [t["id"] for t in tracks if not t["size"]]
    total = sum(t["size"] or 0 for t in tracks)
    return {"tracks": tracks, "playlists": pls, "nodes": nodes,
            "n_tracks": len(tracks),
            "total_bytes": total, "missing_files": len(missing_files)}


def write_sync_ledgers(rb_dir: Path, nodes: list[dict]) -> None:
    """rekordbox's device sync ledgers, playlists3.sync and playlists3Plus.sync.

    Plain XML naming the library and every playlist node on the device.  Two
    byte-identical copies, CRLF throughout, trailing newline.  rekordbox
    writes these on export and reads them back to work out what a device
    already holds; deleting them is what triggers its multi-hour skeleton
    rebuild, so a device that has none may well look wrong to it.

    Ids are the library's own playlist ids in uppercase hex, Dev_ID is the
    stick-side sequential id, Attribute is 1 for a folder and 0 for a
    playlist, and CheckType follows: 2 for folders, 1 for playlists.  The
    root row is always Id 0 with Dev_ID 0.
    """
    ms = int(time.time() * 1000)
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', '',
             f'<Sync DBID="{MASTER_DB_ID}" AutomaticSync="1" AllPlaylists="0"'
             ' IncludeCue="1" ForcedSync="0" Timestamp="0">',
             '  <Playlists>',
             '    <NODE Id="0" ParentId="0" Attribute="1" Lib_Type="0"'
             ' Dev_ID="0" Timestamp="0" CheckType="2"/>']
    for nd in nodes:
        folder = bool(nd["is_folder"])
        lines.append(
            f'    <NODE Id="{nd["lib_id"]:X}" ParentId="{nd["parent_lib_id"]:X}"'
            f' Attribute="{1 if folder else 0}" Lib_Type="0"'
            f' Dev_ID="{nd["sid"]}" Timestamp="{ms}"'
            f' CheckType="{2 if folder else 1}"/>')
    lines += ['  </Playlists>', '</Sync>', '']
    blob = "\r\n".join(lines).encode("utf-8")
    for name in ("playlists3.sync", "playlists3Plus.sync"):
        (rb_dir / name).write_bytes(blob)


def _build_onelibrary(dst: Path, tracks: list[dict], nodes: list[dict],
                      tags_by_track: dict, banks: list[tuple], log,
                      images: list[tuple] | None = None, beat=None,
                      pairs: list | None = None) -> None:
    template = CORPUS / "deck-verified/rekordbox/exportLibrary.db"
    if not template.exists():
        template = CORPUS / "rekordbox/exportLibrary.db"
    shutil.copy2(template, dst)
    db = sqlcipher3.connect(str(dst))
    db.execute(f"PRAGMA key = '{DLP_KEY}'")
    # myTag is deliberately NOT cleared. The template carries rekordbox's own
    # canonical My Tag table — the real banks, ids and ordering, straight out
    # of a genuine export. We used to delete it and write our own, which
    # dropped a whole bank and renumbered the rest; rekordbox restored its
    # version every time it saw the drive. Its own exports carry no
    # myTag_content at all, so that one is cleared and left empty.
    for t in ("content", "artist", "album", "genre", "label", "key", "playlist",
              "playlist_content", "myTag_content", "history",
              "history_content", "recommendedLike", "cue", "image",
              "hotCueBankList", "hotCueBankList_cue"):
        db.execute(f"DELETE FROM {t}")

    def idmap(table, idcol, names, cols="(%s, name)"):
        m, rows = {}, []
        for i, n in enumerate(sorted({x for x in names if x}), start=1):
            m[n] = i
            rows.append((i, n))
        if table == "artist":
            db.executemany(f"INSERT INTO {table} ({idcol}, name, nameForSearch) VALUES (?,?,NULL)", rows)
        else:
            db.executemany(f"INSERT INTO {table} ({idcol}, name) VALUES (?,?)", rows)
        return m

    artists = idmap("artist", "artist_id", [t["artist"] for t in tracks])
    albums = idmap("album", "album_id", [t["album"] for t in tracks])
    keys = idmap("key", "key_id", [t["key"] for t in tracks])
    labels = idmap("label", "label_id", [t["label"] for t in tracks])

    today = date.today().isoformat()
    for i, t in enumerate(tracks, start=1):
        # a 19k-track insert takes long enough that a silent worker looks
        # stale to the panel, so it checks in as it goes
        if beat and i % 500 == 0:
            beat(i / max(1, len(tracks)))
        ext = Path(t["path"] or "").suffix.lower()
        # Column fidelity matters: rekordbox refuses a device whose OneLibrary
        # deviates from what it writes itself.  Two rules, both measured
        # against real exports (engine/diff_onelibrary.py):
        #   * absent lookups are NULL, never 0 — rekordbox uses 0 only for
        #     color_id and artist_id_lyricist; a 0 in album/genre/label/image
        #     is a dangling foreign key it never produces.
        #   * columns rekordbox always populates must be populated.
        br, depth, srate = audio_props(t["path"])
        db.execute(
            "INSERT INTO content (content_id, title, subtitle, bpmx100, length, trackNo, "
            "discNo, artist_id_artist, artist_id_lyricist, album_id, genre_id, label_id, "
            "key_id, color_id, image_id, djComment, rating, releaseYear, releaseDate, "
            "dateCreated, dateAdded, path, fileName, fileSize, fileType, bitrate, "
            "bitDepth, samplingRate, isrc, djPlayCount, isHotCueAutoLoadOn, "
            "isKuvoDeliverStatusOn, kuvoDeliveryComment, masterDbId, masterContentId, "
            "analysisDataFilePath, analysedBits, contentLink, hasModified, "
            "cueUpdateCount, analysisDataUpdateCount, informationUpdateCount) "
            "VALUES (?,?,?,?,?,1,1,?,0,?,?,?,?,0,?,?,?,?,'',?,?,?,?,?,?,?,?,?,'',0,1,"
            "1,'',?,?,?,?,?,0,NULL,NULL,NULL)",
            (i, t["title"] or Path(t["path"] or "x").stem, "",
             int(round((t["bpm"] or 0) * 100)), int(t["length"] or 0),
             artists.get(t["artist"]) or None, albums.get(t["album"]) or None,
             None, labels.get(t["label"]) or None, keys.get(t["key"]) or None,
             t.get("image_id") or None,
             t["comment"] or "", t["rating"] or 0, t["year"],
             today, today, t["stick_path"], Path(t["path"]).name,
             t["size"] or 0, FILETYPE.get(ext, 0), br, depth, srate,
             MASTER_DB_ID, int(t["id"]), t["stick_anlz"], ANALYSED_BITS, CONTENT_LINK))
        t["content_id"] = i

    cid_of = {t["id"]: t["content_id"] for t in tracks}
    for nd in nodes:
        db.execute("INSERT INTO playlist (playlist_id, sequenceNo, name, image_id, "
                   "attribute, playlist_id_parent) VALUES (?,?,?,NULL,?,?)",
                   (nd["sid"], nd["sort"] + 1, nd["name"],
                    1 if nd["is_folder"] else 0, nd["parent_sid"]))
        for seq, tid in enumerate(nd["track_ids"], start=1):
            if tid in cid_of:
                db.execute("INSERT INTO playlist_content VALUES (?,?,?)",
                           (nd["sid"], cid_of[tid], seq))
    # Matching: pairs computed over the tracks on THIS device, so a partial
    # sync still ships a usable list instead of the handful that survive
    # filtering the library-wide set. Row shape is the deck-verified one from
    # push_matching_to_usb.py: rating NULL, createdDate in milliseconds.
    if pairs:
        import time as _t
        ms = int(_t.time() * 1000)
        rows = [(cid_of[a], cid_of[b], None, ms) for a, b, _sim in pairs
                if a in cid_of and b in cid_of]
        db.executemany("INSERT INTO recommendedLike "
                       "(content_id_1, content_id_2, rating, createdDate) "
                       "VALUES (?,?,?,?)", rows)
        log(f"matching: {len(rows)} pairs written to exportLibrary.db")

    for iid, _apath, bpath in (images or []):
        # the two databases reference twin files: export.pdb the a-file,
        # OneLibrary the b-file.  Neither stores the _m high-res path.
        db.execute("INSERT INTO image (image_id, path) VALUES (?,?)", (iid, bpath))

    # (My Tags intentionally left as the template wrote them — see above.)

    db.execute("UPDATE property SET numberOfContents = ?, createdDate = ?",
               (len(tracks), today))
    db.commit()
    db.close()
    return {"artists": artists, "albums": albums, "keys": keys, "labels": labels}


def start(library_db: Path, playlist_ids: list[str], volume: str) -> None:
    prev = JOBS.get(volume)
    if prev and prev.get("state") == "running":
        hb_age = time.time() - prev.get("heartbeat", 0)
        raise RuntimeError(
            f"a sync to {volume} is already running "
            f"(last heartbeat {hb_age:.0f}s ago"
            + (" — looks stale, kill it from the sync panel)" if hb_age > 20 else ")"))
    vol = Path("/Volumes") / volume
    if not vol.exists() or volume in EXCLUDE_VOLUMES:
        raise RuntimeError(f"volume {volume} not available as a target")
    job = dict(state="running", step="planning", done=0, total=0,
               bytes_done=0, bytes_total=0, current="", log=[], cancel=False,
               abort=False, started=time.time(), heartbeat=time.time(),
               volume=volume, _library_db=str(library_db))
    JOBS[volume] = job
    threading.Thread(target=_run, args=(job, library_db, playlist_ids, vol),
                     daemon=True).start()


def _run(job: dict, library_db: Path, playlist_ids: list[str], vol: Path) -> None:
    def alive() -> bool:
        """Abort silently if this worker was killed/superseded; fail loudly if
        the target volume vanished (yanked mid-sync)."""
        if job.get("abort") or JOBS.get(vol.name) is not job:
            return False
        job["heartbeat"] = time.time()
        if not vol.exists():
            raise OSError(f"target volume {vol.name} disappeared mid-sync")
        return True

    log = job["log"].append
    _t0 = time.time()
    _last = [_t0]

    def mark(step_done: str):
        now = time.time()
        log(f"⏱ {step_done}: {now - _last[0]:.1f}s")
        _last[0] = now
    try:
        from .build_info import build_id
        job["build"] = build_id()
        log(f"engine build: {job['build']}")
        p = plan(library_db, playlist_ids)
        mark("planning")
        tracks = [t for t in p["tracks"] if t["size"]]
        job.update(total=len(tracks), bytes_total=p["total_bytes"])

        # A leftover sync.temp means rekordbox was interrupted part-way through
        # its own device sync. The marker makes players retry an export that is
        # not happening and crash-loop, and whatever it left behind is
        # half-written. Clear it before we write over the top.
        for _pn in (".PIONEER", "PIONEER"):
            _marker = vol / _pn / "rekordbox" / "sync.temp"
            if _marker.exists():
                _marker.unlink(missing_ok=True)
                log("cleared a stale sync.temp — rekordbox had been "
                    "interrupted mid-sync on this drive")

        # destination layout ('.PIONEER' on HFS+ — and the DB paths carry the dot)
        pname = target_pioneer_name(vol)
        job["log"].append(f"library dir: /{pname} ({'hidden, HFS+' if pname.startswith('.') else 'FAT'})")
        for t in tracks:
            # rekordbox's own layout: /Contents/<Artist>/<Album>/<file>, with
            # the literal "UnknownAlbum" when a track has no album.  We used
            # to drop the album level, and because rekordbox decides whether
            # a track is already on a device by looking for it at the path IT
            # would have used, ours were invisible to it: opening a synced
            # drive made it re-export every track alongside our copies, which
            # is the "library conversion" it announces.  The analysis folder
            # address is a hash of this path, so this also puts our ANLZ where
            # rekordbox and the players expect it.
            artist_dir = _safe((t["artist"] or "UnknownArtist").split("/")[0][:60])
            album_dir = _safe((t["album"] or "UnknownAlbum").split("/")[0][:60])
            t["stick_path"] = (f"/Contents/{artist_dir}/{album_dir}/"
                               f"{_safe(Path(t['path']).name)}")
            pv, hr = anlz_hash(t["stick_path"])
            t["stick_anlz"] = f"/{pname}/USBANLZ/P{pv:03X}/{hr:08X}/ANLZ0000.DAT"

        # 1) audio
        linking = link_mode(vol.name)
        job["link_mode"] = bool(linking)
        job["weights"] = phase_weights(
            len(tracks), 0 if linking else p["total_bytes"])
        job["phases"] = {}
        job["progress"] = 0.0
        job["step"] = "linking audio" if linking else "copying audio"
        if linking:
            log(f"link mode: this drive mirrors the library under "
                f"{linking['mirror']}/, so audio is hard-linked instead of copied")
        n_linked = n_copied = 0
        linked_bytes = 0
        for i, t in enumerate(tracks):
            if job["cancel"]:
                job.update(state="cancelled")
                _persist_log(job, library_db)
                return
            if not alive():
                return
            dst = vol / t["stick_path"].lstrip("/")
            job.update(done=i, current=Path(t["path"]).name)
            set_progress(job, "audio", (i + 1) / max(1, len(tracks)))
            if dst.exists() and dst.stat().st_size == t["size"]:
                job["bytes_done"] += t["size"] or 0
                continue
            dst.parent.mkdir(parents=True, exist_ok=True)
            src = link_source(vol, t["path"], t["size"]) if linking else None
            if src is not None:
                try:
                    if dst.exists():
                        dst.unlink()
                    os.link(src, dst)
                    n_linked += 1
                    linked_bytes += t["size"] or 0
                    job["bytes_done"] += t["size"] or 0
                    continue
                except OSError as e:      # different device, permissions, ...
                    log(f"link failed for {Path(t['path']).name} ({e}) — copying")
            if not copy_file(t["path"], dst, t["size"] or 0, job, alive):
                return
            n_copied += 1
        job.update(done=len(tracks), current="")
        set_progress(job, "audio", 1.0)
        job["n_linked"], job["n_copied"] = n_linked, n_copied
        _mb = job["bytes_done"] / 1e6
        _el = max(0.01, time.time() - _last[0])
        if linking:
            log(f"⏱ audio: {_el:.1f}s — {n_linked} hard-linked "
                f"({linked_bytes / 1e9:.2f} GB not copied), {n_copied} copied")
        else:
            log(f"⏱ audio copy: {_el:.1f}s ({_mb / _el:.1f} MB/s effective)")
        _last[0] = time.time()

        # 2) analysis files — the longest phase by far (measured: 202s of a
        # 332s sync), and every track is independent, so it runs on a pool.
        # The work is file reads, a byte splice and file writes, so threads
        # overlap the I/O rather than fighting over the interpreter.
        job["step"] = "copying analysis (waveforms + grids; cue materialization TBD)"

        def one_analysis(t) -> int:
            if not t["anlz_path"]:
                return 0
            src_dir = (RB_SHARE / t["anlz_path"].lstrip("/")).parent
            if not src_dir.exists():
                return 0
            dst_dir = vol / Path(t["stick_anlz"].lstrip("/")).parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            for f in src_dir.iterdir():
                if f.suffix.upper() not in (".DAT", ".EXT", ".2EX"):
                    continue
                (dst_dir / f.name).write_bytes(
                    patch_anlz_path(f.read_bytes(), t["stick_path"]))
            return 1

        copied = seen_n = 0
        with ThreadPoolExecutor(max_workers=ANLZ_WORKERS) as ex:
            futures = [ex.submit(one_analysis, t) for t in tracks]
            for fut in as_completed(futures):
                seen_n += 1
                if job["cancel"] or job.get("abort"):
                    for f2 in futures:
                        f2.cancel()
                    job.update(state="cancelled")
                    _persist_log(job, library_db)
                    return
                try:
                    copied += fut.result()
                except Exception as e:                   # noqa: BLE001
                    log(f"analysis failed for one track: {e}")
                if seen_n % 25 == 0 or seen_n == len(tracks):
                    if not alive():
                        for f2 in futures:
                            f2.cancel()
                        return
                    set_progress(job, "analysis", seen_n / max(1, len(tracks)))
        set_progress(job, "analysis", 1.0)
        log(f"analysis copied for {copied} tracks "
            f"(ANLZ dirs at Pioneer hash paths, {ANLZ_WORKERS} workers)")
        mark("analysis files")

        # 2b) album art, laid out exactly as rekordbox lays it out.  Four
        # files per cover, in a bucket directory, all square baseline JPEGs:
        #
        #     <bucket>/aN.jpg    80px   <- export.pdb artwork row (legacy: XZ)
        #     <bucket>/aN_m.jpg  240px  <- derived by the player, never stored
        #     <bucket>/bN.jpg    80px   <- exportLibrary.db image.path
        #     <bucket>/bN_m.jpg  240px  <- derived by the player, never stored
        #
        # Neither database ever stores an "_m" path.  A player that wants the
        # high-resolution variant takes the path it was given and inserts _m
        # before the extension, so those files must exist under exactly that
        # name or the large art silently resolves to nothing.  The b-files are
        # byte-identical twins of the a-files; only the referencing database
        # differs.  Raw embedded art at arbitrary sizes renders as no art at
        # all, hence the square center-crop and the fixed sizes.
        job["step"] = "album art"
        from .heal import cached_deck_art, deck_jpegs
        art_ids: dict = {}
        images: list[tuple] = []
        cache = cached_deck_art(library_db, [t["id"] for t in tracks])
        for _ai, t in enumerate(tracks):
            if _ai % 25 == 0:
                if not alive():
                    return
                set_progress(job, "artwork", _ai / max(1, len(tracks)))
            hit = cache.get(t["id"])
            if hit:
                h, jpg80, jpg240 = hit
            else:
                data = extract_art(t["path"]) if t["path"] else None   # cache miss
                norm = deck_jpegs(data) if data else None
                if not norm:
                    continue
                import hashlib
                h = hashlib.sha1(data).hexdigest()
                jpg80, jpg240 = norm
            if h not in art_ids:
                aid = len(art_ids) + 1
                bucket = art_bucket(aid)
                adir = vol / pname.lstrip("/") / "Artwork" / bucket
                adir.mkdir(parents=True, exist_ok=True)
                apath = f"/{pname}/Artwork/{bucket}/a{aid}.jpg"   # legacy / pdb
                bpath = f"/{pname}/Artwork/{bucket}/b{aid}.jpg"   # OneLibrary
                for stem, blob in ((f"a{aid}", jpg80), (f"a{aid}_m", jpg240),
                                   (f"b{aid}", jpg80), (f"b{aid}_m", jpg240)):
                    (adir / f"{stem}.jpg").write_bytes(blob)
                art_ids[h] = (aid, apath, bpath)
                images.append((aid, apath, bpath))
            t["artwork_id"] = t["image_id"] = art_ids[h][0]
        set_progress(job, "artwork", 1.0)
        log(f"album art: {len(images)} unique covers across "
            f"{len({art_bucket(i) for i, _a, _b in images})} bucket(s), "
            f"80px + 240px _m variants, for "
            f"{sum(1 for t in tracks if t.get('artwork_id'))} tracks")
        mark("album art")

        # 3) OneLibrary db
        job["step"] = "building exportLibrary.db"
        con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
        tags_by_track: dict[str, list] = {}
        for tid, bank, name in con.execute(
                "SELECT tt.track_id, g.bank, g.name FROM track_tags tt "
                "JOIN tags g ON g.id = tt.tag_id"):
            tags_by_track.setdefault(tid, []).append((bank, name))
        con.close()
        rb_dir = vol / pname / "rekordbox"
        rb_dir.mkdir(parents=True, exist_ok=True)
        # build locally (fast), then one sequential copy to the slow stick
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            local_db = Path(td) / "exportLibrary.db"
            job["step"] = "computing matching pairs for this device"
            try:
                from .matching import stick_pairs
                alive()
                match_pairs = stick_pairs(library_db, [t["id"] for t in tracks],
                                          log=log)
            except Exception as e:                       # noqa: BLE001
                log(f"matching skipped: {e}")
                match_pairs = []
            alive()
            job["step"] = "building exportLibrary.db"

            def _ol_beat(frac):
                alive()
                set_progress(job, "databases", 0.5 * frac)
            maps = _build_onelibrary(local_db, tracks, p["nodes"],
                                     tags_by_track,
                                     ["Genre", "Energy", "Vibe", "Label"], log,
                                     images=images, beat=_ol_beat,
                                     pairs=match_pairs)
            for suf in ("-wal", "-shm"):
                Path(str(rb_dir / "exportLibrary.db") + suf).unlink(missing_ok=True)
            shutil.copy2(local_db, rb_dir / "exportLibrary.db")
            # Read it back FROM THE DEVICE. A database that builds cleanly and
            # then lands damaged is invisible otherwise: the deck reads
            # export.pdb and plays fine while rekordbox and the newer players,
            # which use this file, get nothing. Found exactly that on a drive
            # where an interrupted rekordbox sync had overwritten ours.
            try:
                from .diff_onelibrary import _open as _ol_open
                _chk = _ol_open(rb_dir / "exportLibrary.db")
                _n = _chk.execute("SELECT count(*) FROM content").fetchone()[0]
                if _n != len(tracks):
                    raise AssertionError(
                        f"reads back {_n} tracks, expected {len(tracks)}")
                log(f"exportLibrary.db reads back from the device: {_n} tracks")
            except Exception as e:                       # noqa: BLE001
                raise AssertionError(
                    f"exportLibrary.db did not survive the copy to the device "
                    f"({e}) — the drive may have been interrupted mid-write")

        # 3b) export.pdb — the XDJ-XZ database, corpus-validated writer
        job["step"] = "building export.pdb (XDJ-XZ database)"
        from . import pdb_writer as W
        from .pdb import Pdb
        track_rows, tree_rows, entry_rows = [], [], []
        for t in tracks:
            track_rows.append(W.serialize_track({
                "artwork_id": t.get("artwork_id", 0),
                "file_size": t["size"] or 0, "master_id": int(t["id"]),
                "key_id": maps["keys"].get(t["key"], 0),
                "label_id": maps["labels"].get(t["label"], 0),
                "album_id": maps["albums"].get(t["album"], 0),
                "artist_id": maps["artists"].get(t["artist"], 0),
                "tempo": int(round((t["bpm"] or 0) * 100)),
                "duration": int(t["length"] or 0), "year": t["year"],
                "rating": t["rating"] or 0,
                "bitrate": audio_props(t["path"])[0]
                           or int((t["size"] or 0) * 8 / (t["length"] or 1) / 1000),
                "filetype": {"mp3": 1, "m4a": 4, "aac": 4, "flac": 5, "wav": 11,
                             "aiff": 12, "aif": 12}.get(
                                 Path(t["path"]).suffix.lower().lstrip("."), 1),
                "analyze_path": t["stick_anlz"],
                "comment": t["comment"] or "",
                "title": t["title"] or Path(t["path"]).stem,
                "filename": Path(t["path"]).name,
                "file_path": t["stick_path"], "id": t["content_id"],
            }))
        cid_of = {t["id"]: t["content_id"] for t in tracks}
        for nd in p["nodes"]:
            tree_rows.append(W.serialize_tree(nd["sid"], nd["parent_sid"],
                                              nd["sort"], nd["is_folder"], nd["name"]))
            for seq, tid in enumerate(nd["track_ids"], start=1):
                if tid in cid_of:
                    entry_rows.append(W.serialize_entry(seq, cid_of[tid], nd["sid"]))
        datasets = {
            0: track_rows, 1: [],
            2: [W.serialize_artist(i, n) for n, i in sorted(maps["artists"].items(), key=lambda x: x[1])],
            3: [W.serialize_album(i, 0, n) for n, i in sorted(maps["albums"].items(), key=lambda x: x[1])],
            4: [W.serialize_label(i, n) for n, i in sorted(maps["labels"].items(), key=lambda x: x[1])],
            5: [W.serialize_key(i, n) for n, i in sorted(maps["keys"].items(), key=lambda x: x[1])],
            7: tree_rows, 8: entry_rows,
            13: [W.serialize_artwork(aid, apath) for aid, apath, _b in images],
        }
        tpl = CORPUS / "deck-verified/rekordbox/export.pdb"
        if not tpl.exists():
            tpl = FRESH / "rekordbox/export.pdb"
        set_progress(job, "databases", 0.6)
        alive()
        pdb_bytes = W.build_pdb(tpl, datasets)
        set_progress(job, "databases", 0.85)
        (rb_dir / "export.pdb").write_bytes(pdb_bytes)
        # exportExt: a fresh rekordbox one is library-independent (default My
        # Tags, no track refs) — ship it verbatim
        # Prefer the newest genuine export: the older deck-verified copy
        # carries format version 1 and another library's rows, both of which
        # rekordbox rewrites on sight.
        ext_src = next(
            (c for c in (CORPUS / "rb-converted-2026-09-07/exportExt.pdb",
                         CORPUS / "rb-export-2026-09-07/.PIONEER/rekordbox/exportExt.pdb",
                         CORPUS / "deck-verified/rekordbox/exportExt.pdb",
                         FRESH / "rekordbox/exportExt.pdb") if c.exists()),
            CORPUS / "deck-verified/rekordbox/exportExt.pdb")
        # exportExt carries the legacy Matching table (type 0), which is the
        # one the XDJ-XZ reads — the OneLibrary list is for the newer players.
        # Everything else in the file is cloned from the template, My Tags
        # included, because that table is rekordbox's own and rewriting it
        # brings the conversion prompt back.
        #
        # Row shape and ordering follow push_matching_legacy.py, which is
        # deck-verified: one row per unordered pair, owned by the lower id,
        # grouped by owner and sorted. That script cannot help on a stick we
        # built, because it can only clone an existing populated page and ours
        # start empty; our writer synthesizes the page instead.
        ext_rows = []
        if match_pairs:
            _ms = int(time.time() * 1000)
            _lo, _hi = _ms & 0xFFFFFFFF, _ms >> 32
            by_owner: dict[int, set[int]] = {}
            for a, b, _sim in match_pairs:
                ca, cb = cid_of.get(a), cid_of.get(b)
                if ca is None or cb is None:
                    continue
                by_owner.setdefault(min(ca, cb), set()).add(max(ca, cb))
            for owner in sorted(by_owner):
                for partner in sorted(by_owner[owner]):
                    ext_rows.append(W.serialize_matching(_lo, _hi, partner, owner))
        if ext_src.exists():
            (rb_dir / "exportExt.pdb").write_bytes(
                W.build_pdb(ext_src, {0: ext_rows},
                            build_types={0}, clone_types={1, 2, 3, 4, 5, 6, 7, 8},
                            shift_types=()))
            log(f"exportExt.pdb: {len(ext_rows)} legacy Matching rows "
                f"(what the XZ reads), My Tags cloned from the template")
        # RBFLTR.DAT: rekordbox's device-filter stamp — every real export
        # carries one; clone the deck-verified one (a stale/flag-corrupted
        # leftover here can confuse rekordbox's device scan)
        fltr = CORPUS / "deck-verified/rekordbox/RBFLTR.DAT"
        if fltr.exists():
            shutil.copy2(fltr, rb_dir / "RBFLTR.DAT")
        # sync ledgers: rekordbox writes these on every export and reads them
        # back to see what a device already holds
        write_sync_ledgers(rb_dir, p["nodes"])

        set_progress(job, "databases", 1.0)
        mark("databases (OneLibrary + export.pdb + exportExt)")

        # verification gate: independent re-parse of what we just wrote
        job["step"] = "verifying export.pdb"
        set_progress(job, "finalize", 0.1)
        q = Pdb(pdb_bytes)
        vt = len(q.rows(0)); ve = len(q.playlist_entries()); vp = len(q.playlist_tree())
        assert vt == len(tracks), f"track count {vt} != {len(tracks)}"
        assert vp == len(p["nodes"]), f"tree {vp} != {len(p['nodes'])}"
        got = {t2["id"]: t2 for ro, _ in q.rows(0) for t2 in [q.track(ro)]}
        for t in tracks[:50] + tracks[-50:]:
            g = got[t["content_id"]]
            assert g["strings"][17] == (t["title"] or Path(t["path"]).stem)
            assert g["file_size"] == (t["size"] or 0)
        log(f"export.pdb verified: {vt} tracks, {vp} playlists, {ve} entries re-parse clean")

        # structural gate: the deck reads a pdb permissively, rekordbox opens
        # it with a full DeviceSQL engine and rejects free-page bookkeeping it
        # would not have written itself ("device library is corrupted").
        from .verify_pdb import verify, verify_file
        for label, problems in (("export.pdb", verify(pdb_bytes)),
                                ("exportExt.pdb", verify_file(rb_dir / "exportExt.pdb"))):
            if problems:
                raise AssertionError(
                    f"{label} failed structural validation: " + "; ".join(problems[:5]))
        log("export.pdb + exportExt.pdb pass structural validation "
            "(page chains, spare pages, byte accounting)")

        # and the same for the OneLibrary: rekordbox refuses a device whose
        # database deviates from the shape it writes itself
        try:
            from .diff_onelibrary import _open, dangling
            odb = _open(rb_dir / "exportLibrary.db")
            od = dangling(odb)
            if od:
                raise AssertionError("exportLibrary.db: " + "; ".join(od[:5]))
            log("exportLibrary.db verified: no dangling references")
        except ImportError:
            pass

        # artwork on disk: every referenced cover plus the _m variant each
        # database's consumer derives from it.  A missing _m is invisible in
        # both databases and shows up only as art that will not render.
        missing_art = []
        for _aid, apath, bpath in images:
            for ref in (apath, bpath):
                for cand in (ref, ref.replace(".jpg", "_m.jpg")):
                    if not (vol / cand.lstrip("/")).exists():
                        missing_art.append(cand)
        if missing_art:
            raise AssertionError(
                f"{len(missing_art)} artwork file(s) referenced but not on the "
                f"stick, first: {missing_art[0]}")
        if images:
            log(f"artwork verified: {4 * len(images)} files present for "
                f"{len(images)} covers")

        # 4) player settings, each taken from the most recent rekordbox export
        # that carries it.  This used to pick ONE source directory, and the one
        # it picked has no DEVSETTING.DAT, so we silently shipped sticks
        # without a file every real export has.  The other four settings files
        # are byte-identical across our sources.
        set_progress(job, "finalize", 0.6)
        job["step"] = "player settings"
        missing_settings = []
        for name in SETTINGS_NAMES:
            src = settings_source(name)
            if src is None:
                missing_settings.append(name)
                continue
            shutil.copy2(src, vol / pname / name)
        if missing_settings:
            log(f"settings not found in the corpus: {', '.join(missing_settings)}")
        gcred = FRESH / "extracted/gcred.dat"
        if gcred.exists():
            (vol / pname / "extracted").mkdir(exist_ok=True)
            shutil.copy2(gcred, vol / pname / "extracted/gcred.dat")

        if not alive():
            return
        # manifest: lets the drive settings page repair/re-sync without
        # re-picking playlists (kept OUT of the rekordbox directory)
        import json as _json
        from datetime import datetime, timezone
        (rb_dir / "crate-sync.json").unlink(missing_ok=True)   # legacy location
        manifest_path(vol).write_text(_json.dumps({
            "tool": "crate-constellation",
            "volume": vol.name,
            "synced_at": datetime.now(tz=timezone.utc).isoformat(),
            "playlist_ids": playlist_ids,
            "n_tracks": len(tracks),
            "n_playlists": len(p["playlists"]),
            "n_artwork": len(images),
            "engine_build": job.get("build", "unknown"),
            "link_mode": bool(job.get("link_mode")),
            "n_linked": job.get("n_linked", 0),
        }, indent=1))

        set_progress(job, "finalize", 0.8)
        # flush the page cache so "done" means safe-to-eject (and MB/s is honest)
        job["step"] = "flushing to disk"
        import subprocess
        subprocess.run(["sync"], timeout=600)
        mark("flush")
        total_el = time.time() - _t0
        job["n_playlists"] = len(p["playlists"])  # leaves; nodes incl folders
        set_progress(job, "finalize", 1.0)
        job.update(state="done", step="complete",
                   summary=f"{len(tracks)} tracks · {len(p['playlists'])} playlists · "
                           f"{p['total_bytes'] / 1e9:.1f} GB in {total_el:.0f}s "
                           f"({p['total_bytes'] / 1e6 / max(total_el, 0.01):.1f} MB/s overall)")
        log("export.pdb + exportExt.pdb written by the corpus-validated writer — "
            "first load on the XZ is the remaining real-world test")
        _persist_log(job, library_db)
    except Exception as e:
        if not job.get("abort") and JOBS.get(vol.name) is job:
            job.update(state="error", error=f"{type(e).__name__}: {e}")
            _persist_log(job, library_db)
