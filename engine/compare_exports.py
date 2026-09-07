"""Diff two rekordbox USB exports — ours against one rekordbox wrote itself.

Point it at two mounted volumes or two snapshot directories:

    python -m engine.compare_exports <ours> <theirs>

It reports the things that have actually differed in practice: which files
exist, how artwork is named and sized and bucketed, what the two databases
carry, and which path conventions each side used.  Everything is read-only.
"""

import struct
import sys
from pathlib import Path

from . import pdb as P

TABLE_NAMES = {0: "tracks", 1: "genres", 2: "artists", 3: "albums", 4: "labels",
               5: "keys", 6: "colors", 7: "playlist_tree", 8: "playlist_entries",
               13: "artwork", 16: "columns", 17: "history_playlists",
               18: "history_entries", 19: "history"}


def pioneer_dir(root: Path) -> Path | None:
    for name in (".PIONEER", "PIONEER"):
        if (root / name).is_dir():
            return root / name
    return None


def jpeg_size(path: Path) -> tuple[int, int] | None:
    """Width/height from the SOF marker, without a decoder dependency."""
    try:
        d = path.read_bytes()
    except OSError:
        return None
    i = 2
    while i < len(d) - 9:
        if d[i] != 0xFF:
            return None
        m = d[i + 1]
        if m in (0xD8, 0xD9):
            i += 2
            continue
        ln = struct.unpack_from(">H", d, i + 2)[0]
        if m in (0xC0, 0xC1, 0xC2):
            h, w = struct.unpack_from(">HH", d, i + 5)
            return w, h
        if m == 0xDA:
            return None
        i += 2 + ln
    return None


def art_report(pdir: Path) -> dict:
    root = pdir / "Artwork"
    if not root.is_dir():
        return {"present": False}
    buckets = sorted(p.name for p in root.iterdir() if p.is_dir())
    files = sorted(p for b in root.iterdir() if b.is_dir()
                   for p in b.iterdir() if p.is_file())
    by_shape: dict[str, list] = {}
    for f in files:
        stem = f.stem
        prefix = stem[0] if stem[:1].isalpha() else "?"
        shape = f"{prefix}N_m" if stem.endswith("_m") else f"{prefix}N"
        by_shape.setdefault(shape, []).append(f)
    shapes = {}
    for shape, fs in sorted(by_shape.items()):
        sizes = sorted({jpeg_size(f) for f in fs[:20]} - {None})
        shapes[shape] = {"count": len(fs), "sizes": sizes}
    return {"present": True, "buckets": buckets, "n_files": len(files),
            "shapes": shapes}


def pdb_report(path: Path) -> dict:
    if not path.exists():
        return {"present": False}
    p = P.load(path)
    counts, art_paths, anlz = {}, [], []
    for t in p.tables:
        try:
            n = len(p.rows(t["type"]))
        except Exception:
            n = -1
        if n:
            counts[TABLE_NAMES.get(t["type"], f"type{t['type']}")] = n
    for ro, _ in p.rows(P.T_ARTWORK)[:4]:
        aid = struct.unpack_from("<I", p.data, ro)[0]
        art_paths.append((aid, P.read_string(p.data, ro + 4)[0]))
    withart = 0
    for ro, _ in p.rows(P.T_TRACKS):
        tr = p.track(ro)
        if tr["artwork_id"]:
            withart += 1
        if len(anlz) < 2:
            anlz.append((tr["strings"][20], tr["strings"][14]))
    return {"present": True, "counts": counts, "artwork_paths": art_paths,
            "tracks_with_artwork": withart}


def onelib_report(path: Path) -> dict:
    if not path.exists():
        return {"present": False}
    try:
        from .diff_onelibrary import _open, profile
        db = _open(path)
    except Exception as e:                       # noqa: BLE001
        return {"present": True, "error": str(e)}
    pr = profile(db)
    images = [r for r in db.execute("SELECT image_id, path FROM image LIMIT 4")]
    filled = sorted(c for c, f in pr["nulls"].items() if f is not None and f < 0.01)
    empty = sorted(c for c, f in pr["nulls"].items() if f is not None and f > 0.99)
    return {"present": True, "counts": {k: v for k, v in pr["counts"].items() if v},
            "images": images, "always_filled": filled, "always_null": empty}


def side(root: Path) -> dict:
    pdir = pioneer_dir(root)
    if pdir is None:
        raise SystemExit(f"no PIONEER or .PIONEER directory under {root}")
    rb = pdir / "rekordbox"
    files = sorted(f.name for f in rb.iterdir() if f.is_file()) if rb.is_dir() else []
    contents = [p for p in (root / "Contents").rglob("*") if p.is_file()] \
        if (root / "Contents").is_dir() else []
    depths = sorted({len(p.relative_to(root / "Contents").parts) for p in contents})
    return {"root": root, "pioneer": pdir.name, "rb_files": files,
            "art": art_report(pdir),
            "pdb": pdb_report(rb / "export.pdb"),
            "ext": pdb_report(rb / "exportExt.pdb"),
            "onelib": onelib_report(rb / "exportLibrary.db"),
            "n_audio": len(contents), "contents_depths": depths,
            "pioneer_extra": sorted(p.name for p in pdir.iterdir())}


def show(a: dict, b: dict) -> None:
    def row(label, x, y):
        flag = "" if x == y else "   <-- differs"
        print(f"  {label:<26} {str(x)[:48]:<50} {str(y)[:48]}{flag}")

    print(f"\n{'':28} {'OURS':<50} THEIRS")
    print("-" * 130)
    row("library directory", a["pioneer"], b["pioneer"])
    row("files in rekordbox/", a["rb_files"], b["rb_files"])
    row("other PIONEER entries", a["pioneer_extra"], b["pioneer_extra"])
    row("audio files", a["n_audio"], b["n_audio"])
    row("Contents path depth", a["contents_depths"], b["contents_depths"])

    print("\n-- artwork --")
    for k in ("present", "n_files", "buckets"):
        row(k, a["art"].get(k), b["art"].get(k))
    for shape in sorted(set(a["art"].get("shapes", {})) | set(b["art"].get("shapes", {}))):
        row(f"  {shape}", a["art"].get("shapes", {}).get(shape),
            b["art"].get("shapes", {}).get(shape))

    print("\n-- export.pdb --")
    for k in sorted(set(a["pdb"].get("counts", {})) | set(b["pdb"].get("counts", {}))):
        row(f"  {k}", a["pdb"].get("counts", {}).get(k, 0),
            b["pdb"].get("counts", {}).get(k, 0))
    row("tracks with artwork", a["pdb"].get("tracks_with_artwork"),
        b["pdb"].get("tracks_with_artwork"))
    print("  artwork row paths:")
    for lbl, s in (("ours  ", a), ("theirs", b)):
        for aid, pth in s["pdb"].get("artwork_paths", [])[:3]:
            print(f"    {lbl} id={aid} {pth}")

    print("\n-- exportExt.pdb --")
    for k in sorted(set(a["ext"].get("counts", {})) | set(b["ext"].get("counts", {}))):
        row(f"  {k}", a["ext"].get("counts", {}).get(k, 0),
            b["ext"].get("counts", {}).get(k, 0))

    print("\n-- exportLibrary.db --")
    for k in sorted(set(a["onelib"].get("counts", {})) | set(b["onelib"].get("counts", {}))):
        row(f"  {k}", a["onelib"].get("counts", {}).get(k, 0),
            b["onelib"].get("counts", {}).get(k, 0))
    print("  image rows:")
    for lbl, s in (("ours  ", a), ("theirs", b)):
        for r in s["onelib"].get("images", [])[:3]:
            print(f"    {lbl} {r}")
    onlyb = sorted(set(b["onelib"].get("always_filled", [])) &
                   set(a["onelib"].get("always_null", [])))
    if onlyb:
        print(f"  columns they always fill and we never do: {onlyb}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        raise SystemExit(__doc__)
    show(side(Path(sys.argv[1])), side(Path(sys.argv[2])))
