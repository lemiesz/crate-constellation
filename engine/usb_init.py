"""Prepare a USB stick as a Pioneer device before any playlist is synced.

rekordbox does this itself the first time it exports to a drive: it lays down
a library directory, a set of player settings files the decks read AND write
back to, an empty database, and its own sync ledgers.  A drive that has never
seen rekordbox has none of that, and a sync that drops a library onto a bare
stick produces a device unlike anything either the players or rekordbox would
have made.

So the app asks first.  Plug in a bare or half-formed stick and it offers to
initialize; a stick rekordbox already initialized is recognized and left
alone, because that is already exactly the state we would be creating.

Nothing here overwrites an existing library.  Initialization fills in what is
missing and stops.
"""

import shutil
import subprocess
from pathlib import Path

from .usb_sync import (CORPUS, FRESH, SETTINGS_NAMES, settings_source,
                       write_sync_ledgers)

# Filesystems the players can read.  APFS and NTFS are not among them, and a
# stick carrying one cannot be fixed by writing files to it — it has to be
# reformatted, which we will not do to someone's drive without being asked.
SUPPORTED_FS = ("FAT", "EXFAT", "HFS")

DB_NAMES = ("export.pdb", "exportExt.pdb", "exportLibrary.db")

EMPTY = "empty"                # nothing Pioneer-shaped on the drive
READY = "ready"                # initialized and complete
INCOMPLETE = "incomplete"      # a library directory, but pieces missing
UNSUPPORTED = "unsupported"    # a filesystem no player can read


def filesystem(vol: Path) -> str:
    r = subprocess.run(["diskutil", "info", str(vol)],
                       capture_output=True, text=True)
    for line in r.stdout.splitlines():
        if "File System Personality" in line:
            return line.split(":", 1)[1].strip()
    return "unknown"


def device_state(vol: Path) -> dict:
    """Classify a target drive and say what initialization would add."""
    fs = filesystem(vol)
    supported = any(k in fs.upper() for k in SUPPORTED_FS)
    # derived from the personality we already have, rather than shelling out
    # to diskutil a second time: rekordbox hides the library directory on
    # HFS+ and leaves it visible on FAT
    want = "PIONEER" if "FAT" in fs.upper() else ".PIONEER"
    found = next((n for n in (".PIONEER", "PIONEER") if (vol / n).is_dir()), None)

    out = {"volume": vol.name, "filesystem": fs, "filesystem_supported": supported,
           "expected_library_dir": want, "library_dir": found,
           "missing": [], "notes": []}

    if not supported:
        out["state"] = UNSUPPORTED
        out["notes"].append(
            f"{fs} is not a filesystem Pioneer players can read. Reformat the "
            f"drive as FAT32 or Mac OS Extended before using it.")
        out["can_initialize"] = False
        out["can_sync"] = False
        return out

    if found is None:
        out["state"] = EMPTY
        out["missing"] = list(SETTINGS_NAMES) + ["extracted/gcred.dat",
                                                 "rekordbox/RBFLTR.DAT"] + \
            [f"rekordbox/{n}" for n in DB_NAMES]
        out["can_initialize"] = True
        out["can_sync"] = False
        out["notes"].append("This drive has never been prepared for Pioneer "
                            "players. Initializing writes the player settings "
                            "and an empty library.")
        return out

    if found != want:
        out["notes"].append(
            f"The library directory is named {found} but this filesystem "
            f"wants {want}. Players will not find it. Initializing writes the "
            f"correct one; the existing directory is left in place.")

    pdir = vol / found
    for name in SETTINGS_NAMES:
        if not (pdir / name).exists():
            out["missing"].append(name)
    if not (pdir / "extracted/gcred.dat").exists():
        out["missing"].append("extracted/gcred.dat")
    if not (pdir / "rekordbox/RBFLTR.DAT").exists():
        out["missing"].append("rekordbox/RBFLTR.DAT")
    have_db = [n for n in DB_NAMES if (pdir / "rekordbox" / n).exists()]
    for n in DB_NAMES:
        if n not in have_db:
            out["missing"].append(f"rekordbox/{n}")

    out["has_library"] = len(have_db) == len(DB_NAMES)
    if not out["missing"] and found == want:
        out["state"] = READY
        out["can_initialize"] = False
        out["can_sync"] = True
        out["notes"].append("Already initialized. Nothing to do before syncing.")
    else:
        out["state"] = INCOMPLETE
        out["can_initialize"] = True
        out["can_sync"] = out["has_library"] and found == want
    return out


def initialize(vol: Path, log=None) -> dict:
    """Write whatever a prepared Pioneer device needs and this one lacks.

    Never touches an existing database: a drive rekordbox initialized, or one
    we synced earlier, keeps its library and only gains the missing pieces.
    """
    log = log or (lambda _m: None)
    state = device_state(vol)
    if state["state"] == UNSUPPORTED:
        raise RuntimeError(state["notes"][0])
    if state["state"] == READY:
        log("already initialized — nothing to do")
        return {"initialized": False, "written": [], "state": state}

    pname = state["expected_library_dir"]
    pdir = vol / pname
    rb = pdir / "rekordbox"
    rb.mkdir(parents=True, exist_ok=True)
    (pdir / "extracted").mkdir(exist_ok=True)
    written = []

    for name in SETTINGS_NAMES:                     # deck-writable preferences
        dst = pdir / name
        if dst.exists():
            continue
        src = settings_source(name)
        if src is None:
            log(f"no source for {name} in the corpus — skipped")
            continue
        shutil.copy2(src, dst)
        written.append(name)

    gcred_dst = pdir / "extracted/gcred.dat"
    if not gcred_dst.exists():
        for cand in (CORPUS / "rb-export-2026-09-07/.PIONEER/extracted/gcred.dat",
                     FRESH / "extracted/gcred.dat",
                     CORPUS / "deck-verified/extracted/gcred.dat"):
            if cand.exists():
                shutil.copy2(cand, gcred_dst)
                written.append("extracted/gcred.dat")
                break

    fltr_dst = rb / "RBFLTR.DAT"
    if not fltr_dst.exists():
        for cand in (CORPUS / "rb-export-2026-09-07/.PIONEER/rekordbox/RBFLTR.DAT",
                     CORPUS / "deck-verified/rekordbox/RBFLTR.DAT"):
            if cand.exists():
                shutil.copy2(cand, fltr_dst)
                written.append("rekordbox/RBFLTR.DAT")
                break

    if not all((rb / n).exists() for n in DB_NAMES):
        _write_empty_library(rb, log)
        written += [f"rekordbox/{n}" for n in DB_NAMES
                    if (rb / n).exists()]

    if not (rb / "playlists3.sync").exists():
        write_sync_ledgers(rb, [])
        written += ["rekordbox/playlists3.sync", "rekordbox/playlists3Plus.sync"]

    log(f"initialized {vol.name}: wrote {len(written)} file(s)")
    return {"initialized": True, "written": written, "state": device_state(vol)}


def _write_empty_library(rb: Path, log) -> None:
    """An empty but structurally valid library, so the drive reads as a
    Pioneer device before anything is synced to it."""
    from . import pdb_writer as W
    from .usb_sync import _build_onelibrary

    tpl = CORPUS / "deck-verified/rekordbox/export.pdb"
    if not tpl.exists():
        tpl = FRESH / "rekordbox/export.pdb"
    if not (rb / "export.pdb").exists():
        (rb / "export.pdb").write_bytes(W.build_pdb(tpl, {}))

    if not (rb / "exportExt.pdb").exists():
        ext = CORPUS / "deck-verified/rekordbox/exportExt.pdb"
        if not ext.exists():
            ext = FRESH / "rekordbox/exportExt.pdb"
        shutil.copy2(ext, rb / "exportExt.pdb")

    if not (rb / "exportLibrary.db").exists():
        _build_onelibrary(rb / "exportLibrary.db", [], [], {}, [], log, [])
