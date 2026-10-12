"""First-run state, and capturing the stick-export template from a rekordbox stick.

The USB writer builds export.pdb, exportExt.pdb and OneLibrary from scratch, but
it clones the file header and every table's head page from a genuine rekordbox
export — those pages are never synthesized (see SYNCING.md). That template is
personal library data, so it is not in git: each install captures its own from
a small playlist that rekordbox exported to a stick.

capture_template() only ever READS the stick. It copies into
pdb-corpus/deck-verified/, mirroring the stick's PIONEER directory:

    deck-verified/rekordbox/{export.pdb, exportExt.pdb, exportLibrary.db, RBFLTR.DAT}
    deck-verified/{DEVSETTING.DAT, MYSETTING.DAT, ...}     player settings
    deck-verified/extracted/gcred.dat
    deck-verified/template.json                            where it came from

and then proves the writer can actually build from it before calling it ready.
An existing template is moved aside, never overwritten.

    python -m engine.onboarding /Volumes/<STICK>      # same capture, from a shell
"""

import json
import shutil
import sqlite3
import sys
import tempfile
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
CORPUS = ROOT / "pdb-corpus"
TEMPLATE = CORPUS / "deck-verified"
STATE = ROOT / "app-state.json"
MASTER_DB = Path.home() / "Library/Pioneer/rekordbox/master.db"
LM = Path.home() / "Music/library-management"

DB_FILES = ("export.pdb", "exportExt.pdb", "exportLibrary.db")
REQUIRED = tuple(f"rekordbox/{n}" for n in DB_FILES)
SETTINGS_NAMES = ("DEVSETTING.DAT", "MYSETTING.DAT", "MYSETTING2.DAT",
                  "DJMMYSETTING.DAT", "djprofile.nxs")


# --- app state (first-run flag) -----------------------------------------

def read_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def write_state(**changes) -> dict:
    s = read_state() | changes
    STATE.write_text(json.dumps(s, indent=2))
    return s


# --- status --------------------------------------------------------------

def template_status() -> dict:
    missing = [f for f in REQUIRED if not (TEMPLATE / f).exists()]
    meta = {}
    try:
        meta = json.loads((TEMPLATE / "template.json").read_text())
    except (OSError, ValueError):
        pass
    return {"ready": not missing, "missing": missing,
            "source": meta.get("source"), "captured_at": meta.get("captured_at"),
            "tracks": meta.get("tracks"),
            # templates that predate this module were deck-tested by hand
            "deck_confirmed": meta.get("deck_confirmed", not missing and not meta)}


def library_status(db: Path) -> dict:
    out = {"exists": db.exists(), "tracks": 0, "playlists": 0, "imported_at": None,
           "embeddings": 0}
    if not db.exists():
        return out
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        one = lambda sql: (con.execute(sql).fetchone() or [None])[0]
        out["tracks"] = one("SELECT COUNT(*) FROM tracks") or 0
        out["playlists"] = one("SELECT COUNT(*) FROM playlists WHERE kind != 'folder'") or 0
        out["imported_at"] = one("SELECT value FROM meta WHERE key='imported_at'")
        out["embeddings"] = one("SELECT COUNT(*) FROM embeddings") or 0
    except sqlite3.Error:
        pass
    finally:
        con.close()
    return out


def candidate_sticks() -> list[dict]:
    """Mounted drives carrying a rekordbox export, and whether each can be a template."""
    from .usb import EXCLUDE_VOLUMES, pioneer_dir
    vols = Path("/Volumes")
    out = []
    if not vols.exists():
        return out
    for v in sorted(vols.iterdir()):
        if v.name in EXCLUDE_VOLUMES or v.name.startswith(".") or not v.is_dir():
            continue
        pd = pioneer_dir(v)
        if not pd:
            continue
        problem = source_problem(v)
        out.append({"volume": v.name, "usable": problem is None, "problem": problem,
                    "tracks": _count_tracks(pd / "rekordbox/exportLibrary.db")})
    return out


def status(db: Path) -> dict:
    lib = library_status(db)
    st = read_state()
    return {
        "completed": bool(st.get("onboarding_completed")),
        # open by itself only for someone who has nothing yet
        "show": not st.get("onboarding_completed") and lib["tracks"] == 0,
        "rekordbox": {"master_db": MASTER_DB.exists()},
        "library": lib,
        "extras": {"embeddings": lib["embeddings"] > 0,
                   "constellation": (LM / "viz_arrays.npz").exists()},
        "template": template_status(),
        "sticks": candidate_sticks(),
    }


# --- capture -------------------------------------------------------------

def _count_tracks(onelib: Path) -> int | None:
    """Track count from a stick's OneLibrary, read from an off-stick copy."""
    if not onelib.exists():
        return None
    try:
        import sqlcipher3
        from .usb import DLP_KEY
        with tempfile.TemporaryDirectory() as td:
            for suf in ("", "-wal", "-shm"):
                f = Path(str(onelib) + suf)
                if f.exists():
                    shutil.copy2(f, Path(td) / f.name)
            con = sqlcipher3.connect(str(Path(td) / onelib.name))
            con.execute(f"PRAGMA key = '{DLP_KEY}'")
            n = con.execute("SELECT COUNT(*) FROM content").fetchone()[0]
            con.close()
            return n
    except Exception:
        return None


def source_problem(vol: Path) -> str | None:
    """Why this drive can't be a template, or None if it can."""
    from .usb import pioneer_dir
    pd = pioneer_dir(vol)
    if not pd:
        return "no rekordbox export on this drive"
    missing = [n for n in DB_FILES if not (pd / "rekordbox" / n).exists()]
    if missing:
        return f"export is incomplete (missing {', '.join(missing)})"
    # our own sticks carry a manifest; cloning our output would make the
    # template a copy of itself instead of rekordbox's ground truth
    if (pd / "crate-sync.json").exists() or (pd / "rekordbox/crate-sync.json").exists():
        return "written by Crate Constellation, not rekordbox — export from rekordbox instead"
    return None


def _checkpointed_copy(src: Path, dst: Path) -> None:
    """Copy an SQLCipher db with its WAL, then fold the WAL in (on OUR copy).

    The writer copies the template's main file alone, so rows still sitting in
    a WAL would silently vanish from every stick built from it."""
    import sqlcipher3
    from .usb import DLP_KEY
    for suf in ("", "-wal", "-shm"):
        f = Path(str(src) + suf)
        if f.exists():
            shutil.copy2(f, Path(str(dst) + suf))
    con = sqlcipher3.connect(str(dst))
    con.execute(f"PRAGMA key = '{DLP_KEY}'")
    con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    con.execute("PRAGMA journal_mode=DELETE")
    con.close()
    for suf in ("-wal", "-shm"):
        Path(str(dst) + suf).unlink(missing_ok=True)


def validate_template(tdir: Path) -> list[str]:
    """Prove the writer can build from this template. Returns problems."""
    from . import pdb_writer as W
    from .verify_pdb import verify
    problems = []
    rb = tdir / "rekordbox"
    for name in ("export.pdb", "exportExt.pdb"):
        for p in verify((rb / name).read_bytes()):
            problems.append(f"{name} as exported: {p}")
    try:
        built = W.build_pdb(rb / "export.pdb", {})
        problems += [f"empty export.pdb built from it: {p}" for p in verify(built)]
    except Exception as e:
        problems.append(f"writer could not build from export.pdb: {e}")
    try:
        import sqlcipher3
        from .usb import DLP_KEY
        con = sqlcipher3.connect(str(rb / "exportLibrary.db"))
        con.execute(f"PRAGMA key = '{DLP_KEY}'")
        con.execute("SELECT COUNT(*) FROM content").fetchone()
        con.close()
    except Exception as e:
        problems.append(f"exportLibrary.db does not open as OneLibrary: {e}")
    return problems


def capture_template(vol: Path, replace: bool = False, log=print) -> dict:
    """Copy a rekordbox-exported stick's databases into the local template."""
    from .usb import pioneer_dir
    problem = source_problem(vol)
    if problem:
        raise RuntimeError(f"{vol.name}: {problem}")
    if template_status()["ready"] and not replace:
        raise RuntimeError("a template already exists — use Replace (or --replace) "
                           "to swap it; the old one is kept as a backup")
    pd = pioneer_dir(vol)

    CORPUS.mkdir(exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix="template-", dir=CORPUS))
    try:
        (stage / "rekordbox").mkdir()
        (stage / "extracted").mkdir()
        for name in ("export.pdb", "exportExt.pdb", "RBFLTR.DAT"):
            if (pd / "rekordbox" / name).exists():
                shutil.copy2(pd / "rekordbox" / name, stage / "rekordbox" / name)
        _checkpointed_copy(pd / "rekordbox/exportLibrary.db",
                           stage / "rekordbox/exportLibrary.db")
        copied_settings = []
        for name in SETTINGS_NAMES:
            if (pd / name).exists():
                shutil.copy2(pd / name, stage / name)
                copied_settings.append(name)
        if (pd / "extracted/gcred.dat").exists():
            shutil.copy2(pd / "extracted/gcred.dat", stage / "extracted/gcred.dat")
        log(f"copied {vol.name}'s rekordbox databases and "
            f"{len(copied_settings)} player settings file(s)")

        problems = validate_template(stage)
        if problems:
            raise RuntimeError("the writer can't use this export: " + "; ".join(problems[:5]))
        log("writer built a valid empty export.pdb from it")

        tracks = _count_tracks(stage / "rekordbox/exportLibrary.db")
        (stage / "template.json").write_text(json.dumps({
            "source": vol.name, "captured_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "tracks": tracks, "settings": copied_settings, "deck_confirmed": False,
        }, indent=2))

        backup = None
        if TEMPLATE.exists():
            backup = CORPUS / f"deck-verified.bak-{time.strftime('%Y%m%d-%H%M%S')}"
            TEMPLATE.rename(backup)
        try:
            stage.rename(TEMPLATE)
        except OSError:
            if backup:                      # never leave the install without one
                backup.rename(TEMPLATE)
            raise
        stage = None
        if backup:
            log(f"previous template kept at pdb-corpus/{backup.name}")
        log(f"template ready ({tracks} track(s) in the source export)")
        return {"ok": True, "template": template_status(),
                "backup": f"pdb-corpus/{backup.name}" if backup else None}
    finally:
        if stage is not None:
            shutil.rmtree(stage, ignore_errors=True)


def confirm_deck(confirmed: bool = True) -> dict:
    f = TEMPLATE / "template.json"
    meta = json.loads(f.read_text()) if f.exists() else {}
    meta["deck_confirmed"] = confirmed
    f.write_text(json.dumps(meta, indent=2))
    return template_status()


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    try:
        capture_template(Path(sys.argv[1]), replace="--replace" in sys.argv)
    except RuntimeError as e:
        raise SystemExit(f"error: {e}")
