"""Structural validation of a written export.pdb / exportExt.pdb.

The XDJ-XZ reads a PDB permissively — it walks the table chains and takes what
it finds.  rekordbox opens the same file with a full DeviceSQL engine because
it intends to WRITE to it on the next export, so it validates the free-page
bookkeeping and per-page byte accounting first.  That asymmetry is why a stick
can play perfectly on the deck and still be called corrupt on the desktop.

These are the invariants that a rejected file has been observed to violate, in
this project and in the other from-scratch writers that hit the same dialog.
Cheap to check, so the sync runs them on every file it writes.

    python -m engine.verify_pdb <file-or-volume>
"""

import struct
import sys
from pathlib import Path

from .pdb import PAGE, Pdb

# What an index page stores instead of a first-data-page pointer when its
# table is empty.
INDEX_NO_DATA = 0x03FFFFFF


def effective_footer(n_rows: int) -> int:
    """Declared footer size for a page holding n_rows.

    The physical footer is always whole 36-byte groups, but the size a page
    DECLARES counts the partial group as (2 * rows + 4).  Using the physical
    size makes every multi-group page look about thirty bytes short.
    """
    if n_rows == 0:
        return 4
    full, part = divmod(n_rows, 16)
    return full * 36 if part == 0 else full * 36 + (2 * part + 4)


def verify(data: bytes) -> list[str]:
    """Return a list of structural problems; empty means the file is clean."""
    p = Pdb(data)
    n_pages = len(data) // PAGE
    problems: list[str] = []

    ecs = {t["type"]: t["empty_candidate"] for t in p.tables}
    chains = {t["type"]: p.chain(t["type"]) for t in p.tables}

    seen: dict[int, int] = {}
    for typ, chain in chains.items():
        for pg in chain:
            if pg in seen:
                problems.append(
                    f"page {pg} is in both table {seen[pg]}'s and table {typ}'s chain")
            seen[pg] = typ

    # Each table needs its OWN spare page.  Sharing one blank across tables, or
    # letting a spare land inside another table's chain, fails the desktop's
    # structural check even though the deck never notices.
    for typ, ec in ecs.items():
        others = [o for o, e in ecs.items() if e == ec and o != typ]
        if others:
            problems.append(f"table {typ} shares empty_candidate {ec} with {others}")
        if ec in seen:
            problems.append(
                f"table {typ}'s empty_candidate {ec} is a data page of table {seen[ec]}")
        if ec < n_pages and any(data[ec * PAGE:(ec + 1) * PAGE]):
            problems.append(f"table {typ}'s empty_candidate page {ec} is not all zero")

    if ecs and p.next_unused <= max(ecs.values()):
        problems.append(
            f"next_unused_page {p.next_unused} must exceed every empty_candidate "
            f"(max {max(ecs.values())})")

    for t in p.tables:
        last = t["last"]
        if last >= n_pages:
            problems.append(f"table {t['type']}: last_page {last} is past the end of file")
            continue
        nxt = struct.unpack_from("<I", data, last * PAGE + 12)[0]
        if nxt != t["empty_candidate"]:
            problems.append(
                f"table {t['type']}: chain terminator next_page={nxt}, "
                f"expected empty_candidate={t['empty_candidate']}")

    # A table's head page is an index page whose body carries its own pointer
    # to the first data page.  A writer that clones head pages from a template
    # inherits the template's pointer, so a table the template left empty goes
    # on announcing itself as empty no matter how many rows get written after.
    # Walking the chain still finds those rows, so nothing else notices; a
    # player follows the index and sees an empty table.  This is what hid our
    # album artwork.
    for t in p.tables:
        head = t["first"]
        if head >= n_pages:
            continue
        first_data = next((pg for pg in chains[t["type"]]
                           if not (data[pg * PAGE + 0x1B] & 0x40)), None)
        declared = struct.unpack_from("<I", data, head * PAGE + 0x2C)[0]
        want = INDEX_NO_DATA if first_data is None else first_data
        if declared != want:
            shown = "empty" if declared == INDEX_NO_DATA else f"page {declared}"
            problems.append(
                f"table {t['type']}: index page {head} declares {shown}, "
                f"but the table's first data page is "
                f"{'none' if first_data is None else first_data}")

    for typ, chain in chains.items():
        for pg in chain:
            o = pg * PAGE
            if data[o + 0x1B] & 0x40:            # sentinel / index page
                continue
            rows = data[o + 0x18] + 0x100 * (data[o + 0x19] & 1)
            free, used = struct.unpack_from("<HH", data, o + 0x1C)
            want = PAGE - 40 - effective_footer(rows) - used
            if free != want:
                problems.append(
                    f"table {typ} page {pg}: free_size {free}, expected {want} "
                    f"({rows} rows, used {used})")
    return problems


# Deliberately NOT checked: the row-count word at 0x19.  The "0x20 x rows"
# rule holds only for a page written in a single save; rekordbox's own exports
# violate it on any page that has ever had a deletion, so asserting it flags
# real, deck-accepted files as broken.


def verify_file(path: Path) -> list[str]:
    return verify(Path(path).read_bytes())


def main(argv: list[str]) -> int:
    if not argv:
        raise SystemExit(__doc__)
    target = Path(argv[0])
    files = []
    if target.is_dir():
        for name in (".PIONEER/rekordbox", "PIONEER/rekordbox", "rekordbox", ""):
            d = target / name if name else target
            if (d / "export.pdb").exists():
                files = [d / "export.pdb", d / "exportExt.pdb"]
                break
    else:
        files = [target]
    rc = 0
    for f in files:
        if not f.exists():
            continue
        problems = verify_file(f)
        print(f"{f}: {'clean' if not problems else f'{len(problems)} problem(s)'}")
        for pr in problems:
            print("   *", pr)
        rc |= 1 if problems else 0
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
