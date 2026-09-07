"""Library doctor: artwork precompute cache + heal/repair checks.

Artwork: extracted once per track (parallel), deduped by sha1 into
art-cache/<sha1>.jpg with an `artwork` table in library.db. Syncs then copy
cached files instead of parsing audio tags per write.

Heal: non-destructive integrity + consistency pass over library.db with an
honest per-step report; fixes only what is provably safe to fix.
"""

import hashlib
import os
import sqlite3
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
ART_CACHE = HERE.parent / "art-cache"

ART_JOB: dict = {"state": "idle"}


def _ensure_art_table(con: sqlite3.Connection) -> None:
    con.execute(
        "CREATE TABLE IF NOT EXISTS artwork ("
        "track_id TEXT PRIMARY KEY, sha1 TEXT, ok INTEGER NOT NULL, "
        "extracted_at TEXT DEFAULT (datetime('now')))")


def art_coverage(library_db: Path) -> dict:
    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    try:
        total = con.execute("SELECT COUNT(*) FROM tracks").fetchone()[0]
        try:
            done = con.execute("SELECT COUNT(*) FROM artwork").fetchone()[0]
            with_art = con.execute("SELECT COUNT(*) FROM artwork WHERE ok=1").fetchone()[0]
        except sqlite3.OperationalError:
            done = with_art = 0
        return {"total": total, "scanned": done, "with_art": with_art}
    finally:
        con.close()


def deck_jpegs(data: bytes) -> tuple[bytes, bytes] | None:
    """Normalize a cover to what Pioneer hardware renders: square center-crop,
    80x80 (aN.jpg) + 240x240 (aN_m.jpg), baseline JPEG. rekordbox never ships
    raw embedded art — decks silently show nothing for odd sizes/encodings."""
    try:
        from io import BytesIO
        from PIL import Image
        im = Image.open(BytesIO(data)).convert("RGB")
        w, h = im.size
        s = min(w, h)
        im = im.crop(((w - s) // 2, (h - s) // 2, (w + s) // 2, (h + s) // 2))
        out = []
        for px in (80, 240):
            buf = BytesIO()
            im.resize((px, px), Image.LANCZOS).save(
                buf, "JPEG", quality=90, progressive=False)
            out.append(buf.getvalue())
        return out[0], out[1]
    except Exception:
        return None


def _derivative_paths(sha: str) -> tuple[Path, Path]:
    return ART_CACHE / f"{sha}_80.jpg", ART_CACHE / f"{sha}_240.jpg"


def _ensure_derivatives(sha: str) -> bool:
    """Make sure the deck-normalized 80/240 variants exist for a cached raw."""
    p80, p240 = _derivative_paths(sha)
    if p80.exists() and p240.exists():
        return True
    raw = ART_CACHE / f"{sha}.jpg"
    if not raw.exists():
        return False
    d = deck_jpegs(raw.read_bytes())
    if not d:
        return False
    p80.write_bytes(d[0])
    p240.write_bytes(d[1])
    return True


def precompute_art(library_db: Path, workers: int = 8) -> None:
    """Incremental, parallel embedded-art extraction into the cache."""
    if ART_JOB.get("state") == "running":
        return
    ART_JOB.update(state="running", done=0, total=0, started=time.time())

    def run():
        from .usb_sync import extract_art
        try:
            ART_CACHE.mkdir(exist_ok=True)
            con = sqlite3.connect(library_db)
            _ensure_art_table(con)
            todo = con.execute(
                "SELECT t.id, f.path FROM tracks t "
                "JOIN files f ON f.track_id = t.id "
                "LEFT JOIN artwork a ON a.track_id = t.id "
                "WHERE a.track_id IS NULL AND f.path IS NOT NULL").fetchall()
            ART_JOB["total"] = len(todo)

            def one(row):
                tid, path = row
                data = extract_art(path)
                if not data:
                    return (tid, None)
                sha = hashlib.sha1(data).hexdigest()
                f = ART_CACHE / f"{sha}.jpg"
                if not f.exists():
                    f.write_bytes(data)
                _ensure_derivatives(sha)
                return (tid, sha)

            with ThreadPoolExecutor(max_workers=workers) as ex:
                batch = []
                for tid, sha in ex.map(one, todo):
                    batch.append((tid, sha, 1 if sha else 0))
                    ART_JOB["done"] += 1
                    if len(batch) >= 200:
                        con.executemany(
                            "INSERT OR REPLACE INTO artwork (track_id, sha1, ok) "
                            "VALUES (?,?,?)", batch)
                        con.commit()
                        batch = []
                if batch:
                    con.executemany(
                        "INSERT OR REPLACE INTO artwork (track_id, sha1, ok) "
                        "VALUES (?,?,?)", batch)
                    con.commit()
            # backfill deck-normalized 80/240 variants for raws cached before
            # this step existed (idempotent; skips shas that already have them)
            shas = [r[0] for r in con.execute(
                "SELECT DISTINCT sha1 FROM artwork WHERE ok=1")]
            con.close()
            todo2 = [s for s in shas
                     if not _derivative_paths(s)[1].exists()]
            if todo2:
                ART_JOB.update(phase="normalizing", total=len(todo2), done=0)
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    for _ in ex.map(_ensure_derivatives, todo2):
                        ART_JOB["done"] += 1
            ART_JOB.update(state="done", phase="",
                           duration=round(time.time() - ART_JOB["started"], 1))
        except Exception as e:
            ART_JOB.update(state="error", error=str(e))

    threading.Thread(target=run, daemon=True).start()


def cached_art(library_db: Path, track_ids: list[str]) -> dict[str, tuple[str, bytes]]:
    """track_id -> (sha1, jpeg bytes) for cached tracks in the given set."""
    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    out = {}
    try:
        ph = ",".join("?" * len(track_ids))
        for tid, sha in con.execute(
                f"SELECT track_id, sha1 FROM artwork WHERE ok=1 "
                f"AND track_id IN ({ph})", track_ids):
            f = ART_CACHE / f"{sha}.jpg"
            if f.exists():
                out[tid] = (sha, f.read_bytes())
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
    return out


def cached_deck_art(library_db: Path,
                    track_ids: list[str]) -> dict[str, tuple[str, bytes, bytes]]:
    """track_id -> (sha1, 80px jpeg, 240px jpeg), deck-normalized, for cached
    tracks in the given set. Derives variants on the fly for older cache
    entries that only have the raw image."""
    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    out = {}
    try:
        ph = ",".join("?" * len(track_ids))
        for tid, sha in con.execute(
                f"SELECT track_id, sha1 FROM artwork WHERE ok=1 "
                f"AND track_id IN ({ph})", track_ids):
            if not _ensure_derivatives(sha):
                continue
            p80, p240 = _derivative_paths(sha)
            out[tid] = (sha, p80.read_bytes(), p240.read_bytes())
    except sqlite3.OperationalError:
        pass
    finally:
        con.close()
    return out


def heal(library_db: Path) -> dict:
    """Non-destructive repair pass. Every step reports what it found/fixed."""
    steps = []
    con = sqlite3.connect(library_db)

    def step(name, found, fixed=None, note=""):
        steps.append({"step": name, "found": found, "fixed": fixed, "note": note})

    # 1. sqlite integrity
    ok = con.execute("PRAGMA integrity_check").fetchone()[0]
    step("sqlite integrity", ok, note="" if ok == "ok" else "REBUILD library.db (re-import)")

    # 2. schema completeness (idempotent)
    con.executescript((HERE / "schema.sql").read_text())
    _ensure_art_table(con)
    step("schema", "ensured")

    # 3. missing audio files
    missing = [r[0] for r in con.execute(
        "SELECT f.track_id FROM files f WHERE f.path IS NOT NULL") if False]
    n_missing = 0
    for tid, path in con.execute("SELECT track_id, path FROM files WHERE path IS NOT NULL"):
        if not os.path.exists(path):
            n_missing += 1
    step("audio files missing on disk", n_missing,
         note="relink or re-import to fix paths" if n_missing else "")

    # 4. orphaned links (rows pointing at nonexistent tracks)
    o1 = con.execute("DELETE FROM playlist_tracks WHERE track_id NOT IN "
                     "(SELECT id FROM tracks)").rowcount
    o2 = con.execute("DELETE FROM track_tags WHERE track_id NOT IN "
                     "(SELECT id FROM tracks)").rowcount
    step("orphaned playlist/tag links", o1 + o2, fixed=o1 + o2)

    # 5. energy field vs E-tag consistency (tag is authoritative)
    fixes = 0
    for tid, name in con.execute(
            "SELECT tt.track_id, g.name FROM track_tags tt "
            "JOIN tags g ON g.id = tt.tag_id "
            "WHERE g.bank='Energy' AND g.name GLOB 'E[0-9]*'"):
        try:
            e = int(name[1:])
        except ValueError:
            continue
        cur = con.execute("SELECT energy FROM tracks WHERE id=?", (tid,)).fetchone()
        if cur and cur[0] != e:
            con.execute("UPDATE tracks SET energy=? WHERE id=?", (e, tid))
            fixes += 1
    step("energy field vs E-tag", fixes, fixed=fixes)

    # 6. comment hashtag mirror (tags present but hashtag missing)
    fixes = 0
    rows = con.execute(
        "SELECT tt.track_id, g.name, t.comment FROM track_tags tt "
        "JOIN tags g ON g.id = tt.tag_id JOIN tracks t ON t.id = tt.track_id "
        "WHERE g.bank IN ('Genre','Energy','Vibe','Label')").fetchall()
    by_track: dict = {}
    for tid, name, comment in rows:
        by_track.setdefault(tid, [comment, []])[1].append(name)
    for tid, (comment, names) in by_track.items():
        cur = comment or ""
        low = cur.lower()
        # case-insensitive presence check; skip names with spaces (never valid
        # hashtags — the legacy mirror never wrote them either)
        add = [n for n in names
               if " " not in n and f"#{n.lower()}" not in low]
        if add:
            con.execute("UPDATE tracks SET comment=? WHERE id=?",
                        ((cur.rstrip() + " " + " ".join(f"#{n}" for n in add)).strip(), tid))
            fixes += 1
    step("comment #hashtag mirror", fixes, fixed=fixes)

    # 6b. duplicate hashtags (case-insensitive) — keep first occurrence
    import re as _re
    fixes = 0
    for tid, c in con.execute(
            "SELECT id, comment FROM tracks WHERE comment LIKE '%#%'").fetchall():
        seen = set()

        def _repl(m):
            t = m.group(1)
            if t.lower() in seen:
                return ""
            seen.add(t.lower())
            return m.group(0)
        new = _re.sub(r"\s?#([^\s#]+)", _repl, c)
        new = _re.sub(r"\s{2,}", " ", new).strip()
        if new != c:
            con.execute("UPDATE tracks SET comment=? WHERE id=?", (new, tid))
            fixes += 1
    step("duplicate hashtags", fixes, fixed=fixes)

    # 7. artwork cache coverage (kicks background precompute if incomplete)
    con.commit()
    con.close()
    cov = art_coverage(library_db)
    if cov["scanned"] < cov["total"]:
        precompute_art(library_db)
        step("artwork cache", f"{cov['scanned']}/{cov['total']} scanned",
             note="background precompute started")
    else:
        step("artwork cache", f"complete — {cov['with_art']} tracks with art")

    return {"steps": steps}
