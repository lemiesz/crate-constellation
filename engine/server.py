#!/usr/bin/env python
"""Crate Constellation engine — FastAPI over library.db.

Phase 0/1: read API for the Library screen + static hosting of the built app.
Run:  .venv/bin/uvicorn engine.server:app --port 8765
"""

import re
import sqlite3
import unicodedata
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

HERE = Path(__file__).resolve().parent
DB = HERE.parent / "library.db"
DIST = HERE.parent / "app" / "dist"
RB_SHARE = Path.home() / "Library/Pioneer/rekordbox/share"


def norm(s: str) -> str:
    """Match import_rekordbox.norm: lowercase alphanumerics only ('Jay-Z' -> 'jayz')."""
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]+", "", s.lower())

app = FastAPI(title="Crate Constellation engine")


def q(sql: str, params: tuple = ()) -> list[dict]:
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in con.execute(sql, params).fetchall()]
    finally:
        con.close()


def w(fn):
    """Run fn(con) against library.db read-write, committing on success."""
    con = sqlite3.connect(DB)
    con.row_factory = sqlite3.Row
    try:
        out = fn(con)
        con.commit()
        return out
    finally:
        con.close()


def _hashtag_add(comment: str | None, name: str) -> str:
    tag = f"#{name}"
    cur = comment or ""
    return cur if tag in cur else (cur.rstrip() + " " + tag).strip()


def _hashtag_strip(comment: str | None, name: str) -> str:
    return re.sub(rf"\s*#{re.escape(name)}(?=\s|$)", "", comment or "").strip()


def _journal(con, entity, entity_id, field, old, new):
    con.execute("INSERT INTO edit_journal (entity, entity_id, field, old_value, new_value) "
                "VALUES (?,?,?,?,?)", (entity, entity_id, field, old, new))


@app.post("/api/tracks/{track_id}/tags")
def add_tag(track_id: str, body: dict = Body(...)):
    """Attach an existing catalog tag; mirrors the #hashtag into the comment
    in the same transaction (the standing convention)."""
    tag_id = body.get("tag_id")

    def go(con):
        tag = con.execute("SELECT bank, name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if not tag:
            raise HTTPException(404, "no such tag")
        con.execute("INSERT OR IGNORE INTO track_tags VALUES (?,?)", (track_id, tag_id))
        row = con.execute("SELECT comment FROM tracks WHERE id = ?", (track_id,)).fetchone()
        if not row:
            raise HTTPException(404, "no such track")
        con.execute("UPDATE tracks SET comment = ? WHERE id = ?",
                    (_hashtag_add(row["comment"], tag["name"]), track_id))
        if tag["bank"] == "Energy" and re.fullmatch(r"E(10|[1-9])", tag["name"]):
            con.execute("UPDATE tracks SET energy = ? WHERE id = ?",
                        (int(tag["name"][1:]), track_id))
        _journal(con, "track", track_id, "tag:add", None, f"{tag['bank']}/{tag['name']}")
        return {"ok": True}
    return w(go)


@app.delete("/api/tracks/{track_id}/tags/{tag_id}")
def remove_tag(track_id: str, tag_id: str):
    def go(con):
        tag = con.execute("SELECT bank, name FROM tags WHERE id = ?", (tag_id,)).fetchone()
        if not tag:
            raise HTTPException(404, "no such tag")
        con.execute("DELETE FROM track_tags WHERE track_id = ? AND tag_id = ?",
                    (track_id, tag_id))
        row = con.execute("SELECT comment FROM tracks WHERE id = ?", (track_id,)).fetchone()
        con.execute("UPDATE tracks SET comment = ? WHERE id = ?",
                    (_hashtag_strip(row["comment"], tag["name"]), track_id))
        if tag["bank"] == "Energy" and re.fullmatch(r"E(10|[1-9])", tag["name"]):
            con.execute("UPDATE tracks SET energy = NULL WHERE id = ?", (track_id,))
        _journal(con, "track", track_id, "tag:remove", f"{tag['bank']}/{tag['name']}", None)
        return {"ok": True}
    return w(go)


@app.post("/api/tracks/{track_id}/energy")
def set_energy(track_id: str, body: dict = Body(...)):
    """Set the 1-10 energy: swaps the E-tag in the Energy bank and keeps the
    comment hashtags consistent, one journaled transaction."""
    val = body.get("value")
    if not isinstance(val, int) or not 1 <= val <= 10:
        raise HTTPException(422, "value must be 1-10")

    def go(con):
        old = con.execute("SELECT energy, comment FROM tracks WHERE id = ?",
                          (track_id,)).fetchone()
        if not old:
            raise HTTPException(404, "no such track")
        comment = old["comment"]
        etags = {r["name"]: r["id"] for r in con.execute(
            "SELECT id, name FROM tags WHERE bank = 'Energy' AND name GLOB 'E*'")}
        for name, tid in etags.items():
            if re.fullmatch(r"E(10|[1-9])", name):
                con.execute("DELETE FROM track_tags WHERE track_id = ? AND tag_id = ?",
                            (track_id, tid))
                comment = _hashtag_strip(comment, name)
        new_tag = etags.get(f"E{val}")
        if new_tag:
            con.execute("INSERT OR IGNORE INTO track_tags VALUES (?,?)", (track_id, new_tag))
        comment = _hashtag_add(comment, f"E{val}")
        con.execute("UPDATE tracks SET energy = ?, comment = ? WHERE id = ?",
                    (val, comment, track_id))
        _journal(con, "track", track_id, "energy", str(old["energy"]), str(val))
        return {"ok": True, "energy": val}
    return w(go)


@app.get("/api/journal")
def journal(limit: int = Query(50, le=500)):
    return {"rows": q("SELECT * FROM edit_journal ORDER BY id DESC LIMIT ?", (limit,))}


@app.get("/api/health")
def health():
    return {"ok": True}


@app.get("/api/stats")
def stats():
    s = {r["k"]: r["v"] for r in q(
        "SELECT 'tracks' k, COUNT(*) v FROM tracks "
        "UNION SELECT 'playlists', COUNT(*) FROM playlists WHERE kind != 'folder' "
        "UNION SELECT 'tags', COUNT(*) FROM tags "
        "UNION SELECT 'energy_tagged', COUNT(*) FROM tracks WHERE energy IS NOT NULL")}
    s["imported_at"] = (q("SELECT value FROM meta WHERE key='imported_at'") or
                        [{"value": None}])[0]["value"]
    banks = q("SELECT t.bank, COUNT(DISTINCT t.id) tags, COUNT(tt.track_id) links "
              "FROM tags t LEFT JOIN track_tags tt ON tt.tag_id = t.id "
              "GROUP BY t.bank ORDER BY t.bank")
    s["banks"] = banks
    return s


@app.get("/api/tracks")
def tracks(query: str = "", tag: str = "", playlist: str = "",
           limit: int = Query(200, le=1000), offset: int = 0,
           order: str = "artist"):
    order_sql = {
        "artist": "t.artist COLLATE NOCASE, t.title COLLATE NOCASE",
        "title": "t.title COLLATE NOCASE",
        "bpm": "t.bpm", "energy": "t.energy DESC", "added": "t.added_at DESC",
    }.get(order, "t.artist COLLATE NOCASE")
    where, params = [], []
    if query:
        # fuzzy: normalized tokens must all appear in the search blob; a fully
        # squashed multi-word query also matches ("jay z" -> "jayz")
        tokens = [norm(t) for t in query.split() if norm(t)]
        if tokens:
            likes = ["t.search_blob LIKE ?"] * len(tokens)
            clause = " AND ".join(likes)
            params += [f"%{t}%" for t in tokens]
            if len(tokens) > 1:
                clause = f"({clause}) OR t.search_blob LIKE ?"
                params.append(f"%{''.join(tokens)}%")
            where.append(f"({clause})")
    if tag:
        where.append("t.id IN (SELECT track_id FROM track_tags tt "
                     "JOIN tags g ON g.id = tt.tag_id WHERE g.name = ?)")
        params.append(tag)
    if playlist:
        where.append("t.id IN (SELECT track_id FROM playlist_tracks WHERE playlist_id = ?)")
        params.append(playlist)
    wsql = ("WHERE " + " AND ".join(where)) if where else ""
    total = q(f"SELECT COUNT(*) n FROM tracks t {wsql}", tuple(params))[0]["n"]
    rows = q(
        f"SELECT t.id, t.title, t.artist, t.album, t.label, t.year, t.bpm, t.key, "
        f"t.rating, t.energy, t.length, t.comment, f.path, f.volume "
        f"FROM tracks t LEFT JOIN files f ON f.track_id = t.id "
        f"{wsql} ORDER BY {order_sql} LIMIT ? OFFSET ?",
        tuple(params) + (limit, offset))
    ids = [r["id"] for r in rows]
    tags_by_track: dict[str, list] = {}
    if ids:
        ph = ",".join("?" * len(ids))
        for r in q(f"SELECT tt.track_id, g.bank, g.name FROM track_tags tt "
                   f"JOIN tags g ON g.id = tt.tag_id WHERE tt.track_id IN ({ph})",
                   tuple(ids)):
            tags_by_track.setdefault(r["track_id"], []).append(
                {"bank": r["bank"], "name": r["name"]})
    for r in rows:
        r["tags"] = tags_by_track.get(r["id"], [])
    return {"total": total, "rows": rows}


@app.get("/api/track/{track_id}")
def track(track_id: str):
    rows = q("SELECT t.id, t.title, t.artist, t.album, t.label, t.year, t.bpm, t.key, "
             "t.rating, t.energy, t.length, t.comment, f.path, f.volume "
             "FROM tracks t LEFT JOIN files f ON f.track_id = t.id WHERE t.id = ?",
             (track_id,))
    if not rows:
        raise HTTPException(404, "no such track")
    r = rows[0]
    r["tags"] = q("SELECT g.bank, g.name FROM track_tags tt JOIN tags g ON g.id = tt.tag_id "
                  "WHERE tt.track_id = ?", (track_id,))
    return r


@app.get("/api/playlists")
def playlists():
    rows = q("SELECT p.id, p.name, p.parent_id, p.kind, p.seq, "
             "(SELECT COUNT(*) FROM playlist_tracks pt WHERE pt.playlist_id = p.id) n "
             "FROM playlists p ORDER BY p.seq, p.name")
    return {"rows": rows}


@app.get("/api/tags")
def tags():
    rows = q("SELECT g.id, g.bank, g.name, COUNT(tt.track_id) n FROM tags g "
             "LEFT JOIN track_tags tt ON tt.tag_id = g.id "
             "GROUP BY g.id ORDER BY g.bank, n DESC")
    return {"rows": rows}


# --- similarity: in-memory normalized embedding matrix (lazy) -----------
_emb = {"ids": None, "mat": None, "index": None}


def _emb_matrix():
    if _emb["mat"] is None:
        import numpy as np
        con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
        ids, vecs = [], []
        for tid, vec in con.execute("SELECT track_id, vec FROM embeddings"):
            ids.append(tid)
            vecs.append(np.frombuffer(vec, dtype=np.float32))
        con.close()
        mat = np.vstack(vecs)
        mat /= np.linalg.norm(mat, axis=1, keepdims=True)
        _emb.update(ids=ids, mat=mat, index={t: i for i, t in enumerate(ids)})
    return _emb


@app.get("/api/similar/{track_id}")
def similar(track_id: str, limit: int = Query(12, le=50)):
    import numpy as np
    e = _emb_matrix()
    i = e["index"].get(track_id)
    if i is None:
        return {"rows": [], "reason": "not analyzed yet"}
    sims = e["mat"] @ e["mat"][i]
    order = np.argsort(-sims)
    names = {r["id"]: norm(f"{r['artist'] or ''}{r['title'] or ''}") for r in q(
        "SELECT id, artist, title FROM tracks")}
    seen = {names.get(track_id) or track_id}
    picked = []
    for j in order:
        if j == i:
            continue
        sim = float(sims[j])
        if sim > 0.9995:                      # same-file duplicate
            continue
        tid = e["ids"][j]
        key = names.get(tid) or tid           # collapse duplicate copies of a track
        if key in seen:
            continue
        seen.add(key)
        picked.append((tid, sim))
        if len(picked) >= limit:
            break
    ids = [p[0] for p in picked]
    ph = ",".join("?" * len(ids))
    rows = {r["id"]: r for r in q(
        f"SELECT t.id, t.title, t.artist, t.bpm, t.key, t.energy, f.path "
        f"FROM tracks t LEFT JOIN files f ON f.track_id = t.id WHERE t.id IN ({ph})",
        tuple(ids))} if ids else {}
    out = []
    for tid, sim in picked:
        r = rows.get(tid)
        if r:
            r["sim"] = round(sim, 3)
            out.append(r)
    return {"rows": out}


_wave_cache: dict[str, dict | None] = {}


@app.get("/api/audio/{track_id}")
def audio(track_id: str):
    rows = q("SELECT path FROM files WHERE track_id = ?", (track_id,))
    if not rows or not rows[0]["path"]:
        raise HTTPException(404, "no file for track")
    path = Path(rows[0]["path"])
    if not path.exists():
        raise HTTPException(404, f"file missing: {path}")
    return FileResponse(path)


@app.get("/api/sync/status")
def sync_status():
    """Read-only state of each sync target vs the app library."""
    imported = (q("SELECT value FROM meta WHERE key='imported_at'") or
                [{"value": None}])[0]["value"]
    pending = q("SELECT COUNT(*) n FROM edit_journal WHERE ts > COALESCE(?, '')",
                (imported,))[0]["n"] if imported else 0
    tracks = q("SELECT COUNT(*) n FROM tracks")[0]["n"]

    def fstat(p: Path):
        if not p.exists():
            return None
        st = p.stat()
        return {"size": st.st_size, "mtime": st.st_mtime}

    rb = fstat(Path.home() / "Library/Pioneer/rekordbox/master.db")
    tk = fstat(Path.home() / "Documents/Native Instruments/Traktor 4.5.0/collection.nml")
    usb = []
    vols = Path("/Volumes")
    if vols.exists():
        for v in vols.iterdir():
            if (v / "PIONEER").exists() or (v / "PIONEER DJ").exists():
                usb.append(v.name)
    return {"imported_at": imported, "tracks": tracks, "pending_edits": pending,
            "rekordbox": rb, "traktor": tk, "usb_volumes": usb}


@app.post("/api/sync/import")
def sync_import():
    """Re-seed library.db from rekordbox (read-only against master.db).
    Wipe-and-rebuild for now: local edits newer than the last import are
    overwritten — the UI warns with the pending count first."""
    import subprocess
    import sys as _sys
    r = subprocess.run([_sys.executable, str(HERE / "import_rekordbox.py")],
                       capture_output=True, text=True, timeout=300)
    if r.returncode != 0:
        raise HTTPException(500, r.stderr[-500:] or "import failed")
    global _emb, _wave_cache
    _emb.update(ids=None, mat=None, index=None)   # embedding matrix is stale now
    _wave_cache.clear()
    _constel_cache.clear()
    subprocess.run([_sys.executable, str(HERE / "import_viz.py")],
                   capture_output=True, text=True, timeout=120)
    return {"ok": True, "log": r.stdout.strip()}


# --- USB stick explorer -------------------------------------------------
from . import usb as usbmod


@app.get("/api/usb/status")
def usb_status():
    vol = usbmod.find_stick()
    if not vol:
        return {"mounted": False}
    r = usbmod.scan(vol, DB)
    skip = {"tracks", "playlist_tracks", "by_id", "stick_only_by_cid"}
    return {"mounted": True, **{k: v for k, v in r.items() if k not in skip}}


@app.get("/api/usb/tracks")
def usb_tracks(filter: str = "missing", query: str = "", playlist: str = ""):
    vol = usbmod.find_stick()
    if not vol:
        raise HTTPException(404, "no rekordbox stick mounted")
    r = usbmod.scan(vol, DB)
    if playlist:
        rows = []
        for key in r["playlist_tracks"].get(playlist, []):
            t = r["by_id"].get(key) or r["stick_only_by_cid"].get(key)
            if t:
                rows.append(t)
        if query:
            tokens = [norm(t) for t in query.split() if norm(t)]
            rows = [t for t in rows if all(
                tok in norm(f"{t.get('title') or ''}{t.get('artist') or ''}{t.get('path') or ''}")
                for tok in tokens)]
        return {"total": len(rows), "rows": rows[:500]}   # playlist order preserved
    groups = (["synced", "missing", "stick_only"] if filter == "all" else [filter])
    rows = [t for g in groups for t in r["tracks"].get(g, [])]
    if query:
        tokens = [norm(t) for t in query.split() if norm(t)]
        rows = [t for t in rows if all(
            tok in norm(f"{t.get('title') or ''}{t.get('artist') or ''}{t.get('path') or ''}")
            for tok in tokens)]
    rows.sort(key=lambda t: ((t.get("artist") or "~").lower(), (t.get("title") or "~").lower()))
    return {"total": len(rows), "rows": rows[:500]}


@app.post("/api/usb/patch-matching")
def usb_patch_matching(body: dict = Body(default={})):
    """Phase A sync: push the Matching set to the stick via the deck-verified
    scripts (OneLibrary + legacy exportExt.pdb), backup-first. Dry-run unless
    body.apply is true."""
    import subprocess
    import sys as _sys
    vol = usbmod.find_stick()
    if not vol:
        raise HTTPException(404, "no rekordbox stick mounted")
    apply_flag = ["--apply"] if body.get("apply") else []
    lm = Path.home() / "Music/library-management"
    results = []
    for script in ("push_matching_to_usb.py", "push_matching_legacy.py"):
        r = subprocess.run([_sys.executable, str(lm / script), str(vol)] + apply_flag,
                           capture_output=True, text=True, timeout=600, cwd=lm)
        log = (r.stdout + r.stderr)[-2000:]
        hint = None
        if r.returncode != 0 and "no full data page found" in log:
            hint = ("The stick's exportExt.pdb matching table is empty — the "
                    "deck-verified writer templates an existing page and cannot "
                    "synthesize the first one (gated PDB work). One rekordbox "
                    "device-sync will seed it; re-run the patch afterwards.")
        results.append({"script": script, "ok": r.returncode == 0,
                        "log": log, "hint": hint})
    usbmod._cache.clear()
    return {"ok": all(x["ok"] for x in results),
            "applied": bool(body.get("apply")), "results": results}


# --- USB sync wizard ----------------------------------------------------
from . import usb_sync
from . import usb_init


@app.get("/api/usb/wizard/targets")
def wizard_targets():
    """Target drives, each with the state a sync would find it in.

    A drive rekordbox has never prepared needs initializing first, and the
    wizard says so rather than dropping a library onto a bare stick."""
    rows = usb_sync.targets()
    for r in rows:
        try:
            r["device"] = usb_init.device_state(Path("/Volumes") / r["volume"])
        except Exception as e:                       # noqa: BLE001
            r["device"] = {"state": "unknown", "notes": [str(e)],
                           "can_initialize": False, "can_sync": True}
    return {"rows": rows}


@app.get("/api/usb/device-state")
def usb_device_state(volume: str = ""):
    vol = Path("/Volumes") / volume
    if not volume or not vol.is_dir():
        raise HTTPException(404, f"volume {volume!r} is not mounted")
    return usb_init.device_state(vol)


@app.post("/api/usb/initialize")
def usb_initialize(body: dict = Body(...)):
    """Prepare a drive as a Pioneer device: player settings, an empty library
    and the sync ledgers. Never overwrites an existing library."""
    volume = body.get("volume", "")
    vol = Path("/Volumes") / volume
    if not volume or not vol.is_dir():
        raise HTTPException(404, f"volume {volume!r} is not mounted")
    if volume in usb_sync.EXCLUDE_VOLUMES:
        raise HTTPException(400, f"{volume} is not a valid target")
    log: list[str] = []
    try:
        return usb_init.initialize(vol, log=log.append) | {"log": log}
    except RuntimeError as e:
        raise HTTPException(400, str(e))


@app.get("/api/usb/wizard/state")
def wizard_state(volume: str = ""):
    """Per-library-playlist sync state vs the SELECTED target volume — powers
    the rekordbox-style checked / partial indicators in the wizard."""
    vol = usbmod.find_stick(volume or None)
    if not vol:
        return {"mounted": False, "playlists": {}}
    r = usbmod.scan(vol, DB)
    stick_ids = {t["id"] for t in r["tracks"]["synced"]}
    out = {}
    for row in q("SELECT pt.playlist_id pid, COUNT(*) total, "
                 "SUM(CASE WHEN pt.track_id IN "
                 f"({','.join('?' * len(stick_ids)) or 'NULL'}) THEN 1 ELSE 0 END) got "
                 "FROM playlist_tracks pt GROUP BY pt.playlist_id",
                 tuple(stick_ids)):
        out[row["pid"]] = {"total": row["total"], "on_stick": row["got"] or 0}
    return {"mounted": True, "volume": r["volume"], "playlists": out}


@app.post("/api/usb/wizard/plan")
def wizard_plan(body: dict = Body(...)):
    p = usb_sync.plan(DB, body.get("playlist_ids", []))
    return {"n_tracks": p["n_tracks"], "total_bytes": p["total_bytes"],
            "missing_files": p["missing_files"],
            "playlists": [{"id": x["id"], "name": x["name"], "n": len(x["track_ids"])}
                          for x in p["playlists"]]}


@app.post("/api/usb/wizard/start")
def wizard_start(body: dict = Body(...)):
    try:
        usb_sync.start(DB, body.get("playlist_ids", []), body.get("volume", ""))
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True}


@app.get("/api/events")
async def events():
    """Server-Sent Events: pushes sync-job + art-cache state to every open tab
    the moment it changes (≤250ms latency). EventSource auto-reconnects."""
    import asyncio
    import json as _json
    from fastapi.responses import StreamingResponse
    from . import heal as _heal

    async def gen():
        last = None
        idle_beats = 0
        while True:
            state = {
                "jobs": usb_sync.jobs_snapshot(),
                "art": {k: v for k, v in _heal.ART_JOB.items()},
            }
            payload = _json.dumps(state)
            if payload != last:
                last = payload
                idle_beats = 0
                yield f"data: {payload}\n\n"
            else:
                idle_beats += 1
                if idle_beats >= 60:              # 15s keepalive comment
                    idle_beats = 0
                    yield ": keepalive\n\n"
            await asyncio.sleep(0.25)

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/api/usb/wizard/progress")
def wizard_progress():
    """All jobs, newest first — running and recently finished, per drive."""
    return {"jobs": usb_sync.jobs_snapshot()}


@app.post("/api/usb/wizard/cancel")
def wizard_cancel(body: dict = Body(default={})):
    return usb_sync.cancel(body.get("volume", ""))


@app.post("/api/usb/wizard/dismiss")
def wizard_dismiss(body: dict = Body(default={})):
    """Remove a finished sync from the drawer/notification list."""
    return usb_sync.dismiss(body.get("volume", ""))


@app.get("/api/usb/settings")
def usb_settings(volume: str = ""):
    """Drive settings/maintenance page data: the stick's sync manifest plus
    what's physically in its Artwork dir."""
    vol = usbmod.find_stick(volume or None)
    if not vol:
        return {"mounted": False}
    manifest = usb_sync.read_manifest(vol)
    pdir = usbmod.pioneer_dir(vol)
    art_files = 0
    if pdir:
        art_dir = pdir / "Artwork" / "00001"
        if art_dir.exists():
            art_files = sum(1 for f in art_dir.iterdir()
                            if f.suffix.lower() == ".jpg"
                            and not f.stem.endswith("_m"))
    import shutil as _shutil
    du = _shutil.disk_usage(vol)
    return {"mounted": True, "volume": vol.name, "manifest": manifest,
            "art_files": art_files,
            "free_bytes": du.free, "total_bytes": du.total}


@app.post("/api/usb/resync")
def usb_resync(body: dict = Body(default={})):
    """Repair / re-sync a stick from its own manifest: same playlists, no
    re-picking. Existing audio is skipped, art + ANLZ + DBs are rebuilt —
    this is also how 're-sync album artwork' works."""
    volume = body.get("volume", "")
    vol = usbmod.find_stick(volume or None)
    if not vol:
        raise HTTPException(404, "no stick mounted")
    manifest = usb_sync.read_manifest(vol)
    if not manifest or not manifest.get("playlist_ids"):
        raise HTTPException(409, "no crate-sync.json manifest on this stick — "
                                 "run a normal sync from the wizard once first")
    try:
        usb_sync.start(DB, manifest["playlist_ids"], vol.name)
    except RuntimeError as e:
        raise HTTPException(409, str(e))
    return {"ok": True, "playlists": len(manifest["playlist_ids"])}


@app.post("/api/usb/wizard/kill")
def wizard_kill(body: dict = Body(default={})):
    """Force-release that drive's running/stale sync (debug escape hatch)."""
    return usb_sync.kill(body.get("volume", ""))


@app.get("/api/usb/wizard/log")
def wizard_log(limit: int = Query(30, le=200)):
    """Persistent sync history with speeds — the debug/compare log."""
    try:
        rows = q("SELECT id, started_at, finished_at, volume, state, n_tracks, "
                 "n_playlists, bytes, duration_s, mb_s, error FROM sync_log "
                 "ORDER BY id DESC LIMIT ?", (limit,))
    except Exception:
        rows = []                                # table appears after first sync
    return {"rows": rows}


# --- library doctor -----------------------------------------------------
from . import heal as healmod


@app.post("/api/heal")
def run_heal():
    """Non-destructive repair pass + kicks incremental art precompute."""
    return healmod.heal(DB)


@app.get("/api/heal/art")
def heal_art_status():
    return {**healmod.ART_JOB, "coverage": healmod.art_coverage(DB)}


@app.post("/api/heal/art")
def heal_art_start():
    healmod.precompute_art(DB)
    return {"started": True}


@app.on_event("startup")
def _kick_art_cache():
    """Keep the art cache warm so syncs never parse audio tags at write time."""
    import threading

    def check():
        try:
            # always kick: it's fully incremental (skips extracted tracks AND
            # already-derived 80/240 deck variants), so a warm cache costs one
            # query + stat sweep
            if healmod.art_coverage(DB)["total"]:
                healmod.precompute_art(DB)
        except Exception:
            pass
    threading.Timer(5.0, check).start()


@app.get("/api/energy")
def energy_stats():
    hist = {r["energy"]: r["n"] for r in q(
        "SELECT energy, COUNT(*) n FROM tracks WHERE energy IS NOT NULL GROUP BY energy")}
    total = q("SELECT COUNT(*) n FROM tracks")[0]["n"]
    tagged = sum(hist.values())
    return {"hist": [{"e": i, "n": hist.get(i, 0)} for i in range(1, 11)],
            "total": total, "tagged": tagged}


BACKUP_SOURCES = [
    ("rekordbox", Path.home() / "Library/Pioneer/rekordbox", "master.db.backup-*"),
    ("rekordbox", Path.home() / "Library/Pioneer/rekordbox", "master.db"),
    ("traktor", Path.home() / "Documents/Native Instruments/Traktor 4.5.0", "collection.nml"),
    ("traktor", Path.home() / "Documents/Native Instruments/Traktor 4.5.0/Backup", "**/*.nml"),
    ("traktor", Path.home() / "Documents/Native Instruments/Traktor 4.5.0", "collection_backup*.nml"),
    ("usb-stick", Path.home() / "Music/library-management/stick-backups", "*"),
]
_backup_index: dict[str, Path] = {}


@app.get("/api/backups")
def backups():
    """Every snapshot found on disk, grouped by target. Read-only scan."""
    rows = []
    _backup_index.clear()
    for group, base, pat in BACKUP_SOURCES:
        if not base.exists():
            continue
        for f in sorted(base.glob(pat)):
            if not f.is_file():
                continue
            st = f.stat()
            key = f"{group}:{f.name}:{st.st_mtime_ns}"
            _backup_index[key] = f
            rows.append({
                "key": key, "group": group, "name": f.name,
                "size": st.st_size, "mtime": st.st_mtime,
                "live": f.name in ("master.db", "collection.nml"),
            })
    rows.sort(key=lambda r: (-r["live"], -r["mtime"]))
    return {"rows": rows}


@app.post("/api/backups/reveal")
def backups_reveal(body: dict = Body(...)):
    import subprocess
    f = _backup_index.get(body.get("key", ""))
    if not f or not f.exists():
        raise HTTPException(404, "unknown backup (re-list first)")
    subprocess.run(["open", "-R", str(f)], check=False)
    return {"ok": True}


# --- the validated palette from viz_app.py: hue = family, shade = subgenre ---
import colorsys

FAM_C = {"House": "#3987e5", "Techno": "#e66767", "Trance": "#c98500",
         "Bass": "#9085e9", "DnB": "#199e70", "Hip Hop": "#d55181",
         "Ambient": "#008300", "Electro/Hard": "#d95926", "Other": "#5c5b55"}


def _family(g):
    s = str(g).lower()
    if "trance" in s or "goa" in s: return "Trance"
    if "techno" in s or "schranz" in s: return "Techno"
    if "house" in s or ("garage" in s and "uk" not in s): return "House"
    if "hip hop" in s or "rap" in s or "trap" in s or "r&b" in s: return "Hip Hop"
    if ("drum n bass" in s or "jungle" in s or "halftime" in s
            or "neurofunk" in s or "liquid" in s): return "DnB"
    if ("bassline" in s or "dubstep" in s or "grime" in s or "riddim" in s
            or "uk garage" in s or s == "bass music" or "breakbeat" in s
            or "breaks" in s): return "Bass"
    if ("ambient" in s or "downtempo" in s or "chill" in s or "experimental" in s
            or "idm" in s or "drone" in s or "new age" in s or "trip hop" in s): return "Ambient"
    if ("hardcore" in s or "hardstyle" in s or "gabber" in s or "electro" in s
            or "edm" in s or "dance" in s or "dub" in s): return "Electro/Hard"
    return "Other"


def _shade(hexc, rank):
    if rank == 0:
        return hexc
    r, g, b = (int(hexc[i:i + 2], 16) / 255 for i in (1, 3, 5))
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    if l < 0.35:
        l2 = l + {1: 0.14, 2: 0.27, 3: 0.40, 4: -0.08}[rank]
    else:
        anchor = min(max(l, 0.40), 0.60)
        l2 = anchor + {1: 0.15, 2: -0.14, 3: 0.28, 4: -0.24}[rank]
    l2 = min(max(l2, 0.16), 0.86)
    s2 = sat if l2 <= l else min(sat, 0.72)
    r, g, b = colorsys.hls_to_rgb(h, l2, s2)
    return "#%02x%02x%02x" % tuple(int(v * 255) for v in (r, g, b))


_constel_cache: dict = {}


@app.get("/api/constellation")
def constellation():
    """All plotted points with the validated per-subgenre colors:
    rows = [id, x, y, genre, label, artist, bpm, energy, color]."""
    if _constel_cache:
        return _constel_cache
    rows = q("SELECT v.track_id, v.x, v.y, v.genre, "
             "COALESCE(t.title, REPLACE(f.path, RTRIM(f.path, REPLACE(f.path, '/', '')), '')) lbl, "
             "t.artist, t.bpm, t.energy "
             "FROM viz_cache v JOIN tracks t ON t.id = v.track_id "
             "LEFT JOIN files f ON f.track_id = v.track_id")
    # per-family subgenre ranks by frequency -> shaded colors, exactly as viz_app.py
    from collections import Counter
    fam_of = {g: _family(g) for g in {r["genre"] for r in rows}}
    fam_counts = Counter(fam_of[r["genre"]] for r in rows)
    fam_order = [f for f, _ in fam_counts.most_common() if f != "Other"] + ["Other"]
    genre_c = {}
    for f in fam_order:
        subs = Counter(r["genre"] for r in rows if fam_of[r["genre"]] == f)
        for rank, (g, _) in enumerate(subs.most_common()):
            genre_c[g] = FAM_C["Other"] if f == "Other" else \
                _shade(FAM_C[f], rank if rank < 5 else 0)
    out = {"rows": [[r["track_id"], round(r["x"], 3), round(r["y"], 3), r["genre"],
                     r["lbl"], r["artist"], r["bpm"], r["energy"],
                     genre_c.get(r["genre"], FAM_C["Other"])] for r in rows],
           "legend": [[f, FAM_C[f]] for f in fam_order]}
    _constel_cache.update(out)
    return out


@app.post("/api/reveal/{track_id}")
def reveal(track_id: str):
    """Reveal the track's file in Finder (local app affordance)."""
    import subprocess
    rows = q("SELECT path FROM files WHERE track_id = ?", (track_id,))
    if not rows or not rows[0]["path"] or not Path(rows[0]["path"]).exists():
        raise HTTPException(404, "file missing")
    subprocess.run(["open", "-R", rows[0]["path"]], check=False)
    return {"ok": True}


@app.get("/api/waveform/{track_id}")
def waveform(track_id: str):
    """rekordbox preview waveform (ANLZ PWAV): 400 columns of height 0-31 +
    color 0-7. Read straight from the .DAT rekordbox already computed."""
    if track_id in _wave_cache:
        return _wave_cache[track_id]
    rows = q("SELECT anlz_path FROM files WHERE track_id = ?", (track_id,))
    result = None
    if rows and rows[0]["anlz_path"]:
        dat = RB_SHARE / rows[0]["anlz_path"].lstrip("/")
        if dat.exists():
            try:
                from pyrekordbox.anlz import AnlzFile
                tag = AnlzFile.parse_file(dat).get_tag("PWAV")
                heights, colors = tag.get()
                result = {"heights": [int(x) for x in heights],
                          "colors": [int(x) for x in colors]}
            except Exception:
                result = None
    _wave_cache[track_id] = result
    return result


# --- static frontend (built app) ---------------------------------------
if DIST.exists():
    app.mount("/assets", StaticFiles(directory=DIST / "assets"), name="assets")

    @app.get("/")
    def index():
        # hashed assets cache forever; the shell must always revalidate so a
        # rebuilt bundle is picked up on plain reload
        return FileResponse(DIST / "index.html",
                            headers={"Cache-Control": "no-cache"})
