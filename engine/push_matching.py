"""Push Matching onto a stick that already exists — including one rekordbox made.

The old scripts in library-management/ could only do half of this. The
OneLibrary half worked; the legacy half aborted with "no full data page found
to use as template" on any stick whose Matching table was empty, which is
every fresh export, because it can only clone an existing populated page.
Our writer synthesizes the page, so this handles both.

It also computes the pairs over the tracks ACTUALLY ON THE STICK rather than
filtering a library-wide set, so a stick holding part of the library still
gets a full Matching list instead of the handful whose partners happen to be
aboard.

    python -m engine.push_matching /Volumes/NAME            # dry run
    python -m engine.push_matching /Volumes/NAME --apply

Writes both tables: exportLibrary.db's recommendedLike for the newer players
and exportExt.pdb's type-0 table, which is the one an XDJ-XZ reads. Backs up
what it replaces into a directory OFF the stick, because rekordbox's own
folder should hold rekordbox's files and nothing else.
"""

import datetime
import shutil
import struct
import sys
from pathlib import Path

from . import pdb as P
from . import pdb_writer as W
from .diff_onelibrary import _open as open_onelibrary
from .matching import stick_pairs

HERE = Path(__file__).resolve().parent
BACKUPS = HERE.parent.parent / "library-management/stick-backups"


def pioneer_dir(vol: Path) -> Path:
    for name in (".PIONEER", "PIONEER"):
        if (vol / name / "rekordbox").is_dir():
            return vol / name
    raise SystemExit(f"no PIONEER or .PIONEER/rekordbox on {vol}")


def stick_tracks(rb: Path) -> dict[str, int]:
    """{our library track id: the stick's own content id}.

    Taken from the OneLibrary when present, since it names masterContentId
    outright; otherwise from export.pdb, whose track rows carry the master id
    in the u32 at offset 0x14.
    """
    db_path = rb / "exportLibrary.db"
    if db_path.exists():
        try:
            db = open_onelibrary(db_path)
            rows = db.execute("SELECT content_id, masterContentId FROM content "
                              "WHERE masterContentId IS NOT NULL").fetchall()
            if rows:
                return {str(m): c for c, m in rows}
        except Exception as e:                       # noqa: BLE001
            print(f"   (exportLibrary.db unreadable: {e}; falling back to export.pdb)")
    p = P.load(rb / "export.pdb")
    return {str(p.track(ro)["u14"]): p.track(ro)["id"] for ro, _ in p.rows(P.T_TRACKS)}


def run(vol: Path, apply: bool = False) -> dict:
    """Do the work and return what happened, so the UI can show it as
    something other than a wall of log text."""
    out: dict = {"volume": vol.name, "applied": apply, "ok": True,
                 "steps": [], "notes": []}

    def step(name, ok, detail):
        out["steps"].append({"name": name, "ok": ok, "detail": detail})
        if not ok:
            out["ok"] = False

    rb = pioneer_dir(vol) / "rekordbox"
    library_db = HERE.parent / "library.db"

    ids = stick_tracks(rb)
    out["tracks_on_stick"] = len(ids)

    pairs = stick_pairs(library_db, list(ids), log=out["notes"].append)
    out["pairs"] = len(pairs)
    out["tracks_matched"] = len({x for p in pairs for x in p[:2]})
    if not pairs:
        out["notes"].append("no pairs to write for the tracks on this stick")
        return out

    ms = int(datetime.datetime.now().timestamp() * 1000)
    ol_rows = [(ids[a], ids[b], None, ms) for a, b, _s in pairs
               if a in ids and b in ids]
    by_owner: dict[int, set[int]] = {}
    for a, b, _s in pairs:
        ca, cb = ids.get(a), ids.get(b)
        if ca is not None and cb is not None:
            by_owner.setdefault(min(ca, cb), set()).add(max(ca, cb))
    lo, hi = ms & 0xFFFFFFFF, ms >> 32
    ext_rows = [W.serialize_matching(lo, hi, p, o)
                for o in sorted(by_owner) for p in sorted(by_owner[o])]
    out["onelibrary_rows"] = len(ol_rows)
    out["legacy_rows"] = len(ext_rows)
    if not apply:
        return out

    BACKUPS.mkdir(parents=True, exist_ok=True)
    stamp = datetime.datetime.now().strftime("%Y%m%d-%H%M%S")
    for name in ("exportLibrary.db", "exportExt.pdb"):
        if (rb / name).exists():
            shutil.copy2(rb / name, BACKUPS / f"{name}.bak-{stamp}")
    out["backups"] = str(BACKUPS)

    db = None
    if (rb / "exportLibrary.db").exists():
        try:
            import sqlcipher3
            from .diff_onelibrary import DLP_KEY
            db = sqlcipher3.connect(str(rb / "exportLibrary.db"))
            db.execute(f"PRAGMA key = '{DLP_KEY}'")
            db.execute("DELETE FROM recommendedLike")
            db.executemany("INSERT INTO recommendedLike (content_id_1, "
                           "content_id_2, rating, createdDate) VALUES (?,?,?,?)",
                           ol_rows)
            db.commit()
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.close()
            step("exportLibrary.db", True,
                 f"{len(ol_rows)} pairs — read by CDJ-3000, XDJ-AZ, OPUS")
        except Exception as e:                       # noqa: BLE001
            step("exportLibrary.db", False, str(e))

    ext = rb / "exportExt.pdb"
    if ext.exists():
        built = W.build_pdb(ext, {0: ext_rows}, build_types={0},
                            clone_types={1, 2, 3, 4, 5, 6, 7, 8}, shift_types=())
        check = P.Pdb(built)
        got = len(check.rows(0))
        if got != len(ext_rows):
            step("exportExt.pdb", False,
                 f"re-parse found {got} rows, expected {len(ext_rows)} — "
                 f"stick left untouched")
            return out
        ext.write_bytes(built)
        step("exportExt.pdb", True,
             f"{got} rows — the table an XDJ-XZ reads. "
             f"My Tags preserved ({len(check.rows(3))})")
    return out


def main(argv: list[str]) -> int:
    """Command-line front end; the UI calls run() directly."""
    apply = "--apply" in argv
    vols = [a for a in argv if a.startswith("/")]
    if not vols:
        raise SystemExit(__doc__)
    r = run(Path(vols[0]), apply)
    print(f"{'APPLY' if apply else 'DRY-RUN'} — Matching for {r['volume']}")
    print(f"   {r.get('tracks_on_stick', 0)} tracks on the stick")
    for n in r["notes"]:
        print(f"   {n}")
    if "onelibrary_rows" in r:
        print(f"   {r['onelibrary_rows']} OneLibrary rows, "
              f"{r['legacy_rows']} legacy rows")
    for s in r["steps"]:
        print(f"   {'OK  ' if s['ok'] else 'FAIL'} {s['name']}: {s['detail']}")
    if not apply and r.get("pairs"):
        print("\nDry run. Re-run with --apply (rekordbox closed).")
    return 0 if r["ok"] else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
