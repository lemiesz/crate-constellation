"""Dump the complete state of a Pioneer device to a diffable text report.

Point it at a drive before and after something touches it — rekordbox opening
it, a deck playing from it, our own sync — and diff the two reports to see
exactly what changed and where.

Deliberately verbose and deterministic: every file with its size and hash,
then the parsed contents of both databases, so a change inside a database
shows up as a changed row count or value rather than just a changed hash.

    python -m engine.dump_device /Volumes/LORD-AI > before.txt
"""

import hashlib
import struct
import sys
from datetime import datetime, timezone
from pathlib import Path

from . import pdb as P

SKIP_DIRS = {".Spotlight-V100", ".fseventsd", ".TemporaryItems", ".Trashes"}


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pioneer_dir(root: Path) -> Path | None:
    for name in (".PIONEER", "PIONEER"):
        if (root / name).is_dir():
            return root / name
    return None


def inventory(root: Path, out) -> None:
    print("\n== files ==", file=out)
    print(f"{'size':>10}  {'modified (utc)':<20} {'sha256':<16} path", file=out)
    rows = []
    for p in sorted(root.rglob("*")):
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        if not p.is_file():
            continue
        st = p.stat()
        rows.append((st.st_size,
                     datetime.fromtimestamp(st.st_mtime, timezone.utc)
                     .strftime("%Y-%m-%d %H:%M:%S"),
                     sha(p)[:16], str(p.relative_to(root))))
    for size, mtime, digest, rel in rows:
        print(f"{size:>10}  {mtime:<20} {digest:<16} {rel}", file=out)
    print(f"\n{len(rows)} files, {sum(r[0] for r in rows)} bytes total", file=out)


def dump_pdb(path: Path, out) -> None:
    print(f"\n== {path.name} ==", file=out)
    if not path.exists():
        print("  (absent)", file=out)
        return
    p = P.load(path)
    d = p.data
    print(f"  size={len(d)} pages={len(d)//P.PAGE} num_tables={p.num_tables} "
          f"next_unused={p.next_unused} sequence={p.sequence}", file=out)
    for t in p.tables:
        chain = p.chain(t["type"])
        try:
            n = len(p.rows(t["type"]))
        except Exception as e:                       # noqa: BLE001
            n = f"error: {e}"
        head = t["first"]
        idx_ptr = (struct.unpack_from("<I", d, head * P.PAGE + 0x2C)[0]
                   if head * P.PAGE + 0x30 <= len(d) else None)
        idx = "empty" if idx_ptr == 0x03FFFFFF else idx_ptr
        print(f"  type {t['type']:>3}: rows={n:<7} chain={chain} "
              f"empty_candidate={t['empty_candidate']} index_points_at={idx}",
              file=out)
    if p.table(P.T_ARTWORK):
        print("  artwork rows:", file=out)
        for ro, _ in p.rows(P.T_ARTWORK):
            aid = struct.unpack_from("<I", d, ro)[0]
            print(f"    {aid}: {P.read_string(d, ro + 4)[0]}", file=out)
    # Only export.pdb has these; exportExt reuses the same type numbers for
    # entirely different rows, so decoding them there is meaningless and
    # throws on the first string it tries to read.
    if path.name != "export.pdb":
        return
    try:
        if p.rows(P.T_TRACKS):
            print("  tracks:", file=out)
            for ro, _ in p.rows(P.T_TRACKS):
                t = p.track(ro)
                print(f"    id={t['id']} artwork={t['artwork_id']} "
                      f"tempo={t['tempo']} dur={t['duration']} "
                      f"file={t['strings'][19]!r}", file=out)
            print("  playlist tree:", file=out)
            for r in p.playlist_tree():
                print(f"    id={r['id']} parent={r['parent_id']} "
                      f"folder={r['is_folder']} name={r['name']!r}", file=out)
    except Exception as e:                           # noqa: BLE001
        print(f"  row decode failed: {e}", file=out)


def dump_onelibrary(path: Path, out) -> None:
    print(f"\n== {path.name} ==", file=out)
    if not path.exists():
        print("  (absent)", file=out)
        return
    for side in ("-wal", "-shm"):
        s = Path(str(path) + side)
        print(f"  {path.name}{side}: "
              f"{s.stat().st_size if s.exists() else 'absent'}", file=out)
    try:
        from .diff_onelibrary import _open
        db = _open(path)
    except Exception as e:                           # noqa: BLE001
        print(f"  could not open: {e}", file=out)
        return
    tabs = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
    idxs = [r[0] for r in db.execute(
        "SELECT name FROM sqlite_master WHERE type='index' ORDER BY name")]
    print(f"  tables={len(tabs)} indexes={len(idxs)}", file=out)
    print(f"  index names: {idxs}", file=out)
    for t in tabs:
        n = db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0]
        print(f"    {t:<22} {n}", file=out)
    try:
        cols = [c[1] for c in db.execute("PRAGMA table_info(property)")]
        for r in db.execute("SELECT * FROM property"):
            print(f"  property: {dict(zip(cols, r))}", file=out)
    except Exception:                                # noqa: BLE001
        pass
    try:
        print("  image rows:", file=out)
        for r in db.execute("SELECT image_id, path FROM image ORDER BY image_id"):
            print(f"    {r[0]}: {r[1]}", file=out)
        print("  content (id, image_id, path):", file=out)
        for r in db.execute("SELECT content_id, image_id, path FROM content "
                            "ORDER BY content_id"):
            print(f"    {r[0]} img={r[1]} {r[2]}", file=out)
    except Exception as e:                           # noqa: BLE001
        print(f"  content dump failed: {e}", file=out)


def dump(root: Path, out) -> None:
    print(f"device dump: {root}", file=out)
    print(f"taken: {datetime.now(timezone.utc).isoformat()}", file=out)
    pdir = pioneer_dir(root)
    print(f"library directory: {pdir.name if pdir else 'NONE'}", file=out)
    inventory(root, out)
    if pdir is None:
        return
    rb = pdir / "rekordbox"
    dump_pdb(rb / "export.pdb", out)
    dump_pdb(rb / "exportExt.pdb", out)
    dump_onelibrary(rb / "exportLibrary.db", out)
    for name in ("playlists3.sync", "playlists3Plus.sync"):
        f = rb / name
        print(f"\n== {name} ==", file=out)
        print(f.read_text(errors="replace") if f.exists() else "  (absent)",
              file=out)


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    dump(Path(sys.argv[1]), sys.stdout)
