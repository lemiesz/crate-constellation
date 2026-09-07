"""Compare a stick's exportLibrary.db (OneLibrary) against a rekordbox-written
reference export.

rekordbox refuses to open a device whose library deviates from what it writes
itself ("device library is corrupted"), and it never says which part offended
it.  This is the cheap oracle: profile both databases the same way and print
the differences.

Two classes of deviation have actually bitten us:

  * dangling foreign keys — rekordbox uses 0 as "none" ONLY for color_id and
    artist_id_lyricist.  Everywhere else an absent lookup is NULL.  A 0 in
    album_id / genre_id / label_id / image_id points at a row that does not
    exist, which no real export ever does.
  * columns rekordbox always fills — analysedBits, contentLink, isrc,
    isKuvoDeliverStatusOn, kuvoDeliveryComment, releaseDate, artist_id_lyricist.
    We used to leave every one of them NULL.

Usage:
    python -m engine.diff_onelibrary <stick-or-db> [reference-db]
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

import sqlcipher3

DLP_KEY = "r8gddnr4k847830ar6cqzbkk0el6qytmb3trbbx805jm74vez64i5o8fnrqryqls"
CORPUS = Path(__file__).resolve().parent.parent / "pdb-corpus"
# Two rekordbox-written references: the fresh 4-track HFS+ export (same
# rekordbox version, same filesystem as our sticks) and the preserved 12k
# device export.  A column counts as "always filled" only when BOTH fill it,
# which keeps small-sample coincidences out of the report.
DEFAULT_REFS = [CORPUS / "fresh-export/rekordbox/exportLibrary.db",
                CORPUS / "rekordbox/exportLibrary.db"]

# (table, column) -> (referenced table, referenced column)
REFS = [
    ("content", "artist_id_artist", "artist", "artist_id"),
    ("content", "artist_id_remixer", "artist", "artist_id"),
    ("content", "artist_id_originalArtist", "artist", "artist_id"),
    ("content", "artist_id_composer", "artist", "artist_id"),
    ("content", "album_id", "album", "album_id"),
    ("content", "genre_id", "genre", "genre_id"),
    ("content", "label_id", "label", "label_id"),
    ("content", "key_id", "key", "key_id"),
    ("content", "image_id", "image", "image_id"),
    ("playlist_content", "content_id", "content", "content_id"),
    ("playlist_content", "playlist_id", "playlist", "playlist_id"),
]
# rekordbox's own sentinels — 0 here means "none", not a dangling reference
SENTINEL_ZERO = {("content", "color_id"), ("content", "artist_id_lyricist")}


def _open(path: Path):
    """Copy (with any WAL sidecar) to temp and open with the public DLP key."""
    td = tempfile.mkdtemp()
    dst = Path(td) / "x.db"
    shutil.copy(path, dst)
    for ext in ("-wal", "-shm"):
        side = Path(str(path) + ext)
        if side.exists():
            shutil.copy(side, str(dst) + ext)
    db = sqlcipher3.connect(str(dst))
    db.execute(f"PRAGMA key = '{DLP_KEY}'")
    db.execute("SELECT count(*) FROM sqlite_master").fetchone()   # proves the key
    return db


def resolve(arg: str) -> Path:
    """Accept a volume, a .PIONEER dir, or the database file itself."""
    p = Path(arg)
    if p.is_dir():
        for cand in (p / ".PIONEER/rekordbox/exportLibrary.db",
                     p / "PIONEER/rekordbox/exportLibrary.db",
                     p / "rekordbox/exportLibrary.db",
                     p / "exportLibrary.db"):
            if cand.exists():
                return cand
        raise SystemExit(f"no exportLibrary.db under {p}")
    return p


def profile(db) -> dict:
    tabs = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    out = {"tables": tabs, "counts": {}, "nulls": {}, "cols": {}}
    for t in tabs:
        out["counts"][t] = db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        out["cols"][t] = [d[1] for d in db.execute(f'PRAGMA table_info("{t}")')]
    n = out["counts"].get("content", 0)
    for col in out["cols"].get("content", []):
        if not n:
            out["nulls"][col] = None
            continue
        k = db.execute(
            f'SELECT count(*) FROM content WHERE "{col}" IS NULL').fetchone()[0]
        out["nulls"][col] = k / n
    return out


def dangling(db) -> list[str]:
    bad = []
    for tab, col, rt, rc in REFS:
        zero_ok = (tab, col) in SENTINEL_ZERO
        extra = "" if zero_ok else f' AND t."{col}" != 0'
        try:
            n = db.execute(
                f'SELECT count(*) FROM "{tab}" t WHERE t."{col}" IS NOT NULL{extra}'
                f' AND NOT EXISTS (SELECT 1 FROM "{rt}" r WHERE r."{rc}" = t."{col}")'
            ).fetchone()[0]
            zeros = db.execute(
                f'SELECT count(*) FROM "{tab}" WHERE "{col}" = 0').fetchone()[0]
        except sqlcipher3.Error as e:
            bad.append(f"{tab}.{col}: query failed ({e})")
            continue
        if n:
            bad.append(f"{tab}.{col} -> {rt}: {n} rows reference a missing row")
        if zeros and not zero_ok:
            bad.append(f"{tab}.{col}: {zeros} rows are 0 "
                       f"(rekordbox writes NULL for an absent {rt})")
    return bad


def compare(target: Path, refs: list[Path]) -> int:
    tdb = _open(target)
    rps = [profile(_open(r)) for r in refs]
    tp, rp = profile(tdb), rps[0]
    problems = []

    print(f"target    {target}")
    for r in refs:
        print(f"reference {r}")
    print()

    if tp["tables"] != rp["tables"]:
        problems.append("table set differs: "
                        f"missing={sorted(set(rp['tables']) - set(tp['tables']))} "
                        f"extra={sorted(set(tp['tables']) - set(rp['tables']))}")
    for t in sorted(set(tp["tables"]) & set(rp["tables"])):
        if tp["cols"][t] != rp["cols"][t]:
            problems.append(f"{t}: column list differs from the reference")

    ok = tdb.execute("PRAGMA integrity_check").fetchone()[0]
    if ok != "ok":
        problems.append(f"sqlite integrity_check: {ok}")

    print("content rows:", tp["counts"].get("content", 0),
          " references:", [q["counts"].get("content", 0) for q in rps])
    print("\n-- columns the reference always fills but the target leaves NULL --")
    none_found = True
    for col, frac in tp["nulls"].items():
        rfracs = [q["nulls"].get(col) for q in rps]
        if frac is None or any(f is None for f in rfracs):
            continue
        if frac > 0.99 and all(f < 0.01 for f in rfracs):
            print(f"   {col}")
            problems.append(f"{col}: always NULL, every reference populates it")
            none_found = False
    if none_found:
        print("   (none)")

    print("\n-- dangling / sentinel-zero foreign keys --")
    d = dangling(tdb)
    for line in d or ["   (none)"]:
        print("  ", line if d else line.strip())
    problems += d

    print("\n-- verdict --")
    if problems:
        print(f"   {len(problems)} deviation(s) from a rekordbox-written export:")
        for pr in problems:
            print("     *", pr)
        return 1
    print("   matches the reference on every check")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    tgt = resolve(sys.argv[1])
    rfs = ([resolve(a) for a in sys.argv[2:]] or
           [r for r in DEFAULT_REFS if r.exists()])
    sys.exit(compare(tgt, rfs))
