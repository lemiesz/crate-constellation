"""Hot cues and memory cues, from our library into a device's analysis files.

rekordbox keeps cues in its own database and only materializes them into the
analysis files when it exports. Confirmed here by scanning all 63,244 files in
its local share: not one carries a populated cue container. So a device built
by copying those files has no cues, however many the library holds, and the
only fix is to write the containers ourselves.

The cue data itself is mirrored into `library.db` by the importer, so a sync
reads it from there and never opens rekordbox's database. Re-importing
rebuilds the mirror; a library that predates the table simply reports no cues
and syncs exactly as before.
"""

import sqlite3
import struct
from pathlib import Path

from .anlz import cue_container_indexes, replace_tags, tags

# rekordbox's Kind column: 0 is a memory cue, 1..8 are the hot cue slots A..H.
# Measured on this library: 98,005 memory cues and roughly 13,000 in each slot.
MEMORY = 0
HOT_SLOTS = range(1, 9)


def load_cues(library_db: Path) -> dict[str, list[dict]]:
    """Every cue in the library, grouped by track. One query, about 0.3s.

    Returns {} when the library has no cues table yet, so an older library.db
    syncs unchanged instead of failing.
    """
    con = sqlite3.connect(f"file:{library_db}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(
            "SELECT track_id, kind, in_msec, out_msec, color, color_idx, "
            "comment, loop_beats FROM cues ORDER BY track_id, kind, in_msec"
        ).fetchall()
    except sqlite3.OperationalError:
        return {}                       # no cues table: nothing to write
    finally:
        con.close()
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["track_id"], []).append(dict(r))
    return out


def has_cue_containers(data: bytes) -> bool:
    return bool(cue_container_indexes(data))


def container_summary(data: bytes) -> dict:
    """(fourcc, kind) -> number of entries currently in that container."""
    out = {}
    for fourcc, off, _len in tags(data):
        if fourcc == "PCOB":
            out[("PCOB", struct.unpack_from(">I", data, off + 12)[0])] = \
                struct.unpack_from(">H", data, off + 18)[0]
        elif fourcc == "PCO2":
            out[("PCO2", struct.unpack_from(">I", data, off + 12)[0])] = \
                struct.unpack_from(">H", data, off + 16)[0]
    return out


# rekordbox's Kind column maps onto hot cue slots with 4 skipped: it means
# "loop" and is never a slot. Verified across 203,066 rows, none of which
# carry it, and by matching one track's rows to the slots in its own export.
KIND_TO_HOT = {1: 1, 2: 2, 3: 3, 5: 4, 6: 5, 7: 6, 8: 7, 9: 8}
NO_LOOP = 0xFFFFFFFF
HOT_COLOUR = b"\x00\x1a\xff\x00"      # rekordbox's green; NOT 00ff00
MEMORY_COLOUR = b"\x00\x00\x00\x00"


def prepare(rows: list[dict]) -> tuple[list[dict], list[dict]]:
    """(hot, memory) ready to write: deduped, slot-clamped, newest first.

    This library has 14,171 colliding (track, kind) pairs, one track with 173
    hot-cue rows and 1,099 memory rows. Writing them raw would emit several
    entries for one slot and overflow the format, so a slot keeps its earliest
    position and the rest are dropped.

    Both lists come out ordered by descending time, which is what rekordbox
    writes in every container.
    """
    hot: dict[int, dict] = {}
    memory: list[dict] = []
    for r in rows:
        if r["in_msec"] is None or r["in_msec"] < 0:
            continue
        if r["kind"] == MEMORY:
            memory.append(r)
            continue
        slot = KIND_TO_HOT.get(r["kind"])
        if slot is None:                  # kind 4 (loop) and anything unknown
            continue
        keep = hot.get(slot)
        if keep is None or r["in_msec"] < keep["in_msec"]:
            hot[slot] = {**r, "slot": slot}
    seen, mem = set(), []
    for r in sorted(memory, key=lambda x: x["in_msec"]):
        if r["in_msec"] in seen:
            continue
        seen.add(r["in_msec"])
        mem.append({**r, "slot": 0})
    by_time = lambda xs: sorted(xs, key=lambda x: x["in_msec"], reverse=True)
    return by_time(hot.values()), by_time(mem)


def _pcpt(cue: dict, index: int, count: int, is_hot: bool) -> bytes:
    # Memory lists thread a doubly-linked order chain through the entries;
    # hot lists set both ends to 0xFFFF and carry none.
    first = 0xFFFF if is_hot or index == 0 else index - 1
    last = 0xFFFF if is_hot or index == count - 1 else index + 1
    return (b"PCPT" + struct.pack(">II", 28, 56)
            + struct.pack(">III", cue["slot"], 0, 0x00010000)
            + struct.pack(">HH", first, last)
            + b"\x01\x00\x03\xe8"
            + struct.pack(">II", cue["in_msec"], NO_LOOP)
            + b"\x00" * 16)


def build_pcob(cues: list[dict], is_hot: bool) -> bytes:
    n = len(cues)
    # memory lists store count-1 as a signed value, so empty is 0xFFFFFFFF;
    # hot lists always store 0xFFFFFFFF whatever the count
    memory_count = NO_LOOP if is_hot else (n - 1) & 0xFFFFFFFF
    body = b"".join(_pcpt(c, i, n, is_hot) for i, c in enumerate(cues))
    return (b"PCOB" + struct.pack(">II", 24, 24 + len(body))
            + struct.pack(">IHHI", 1 if is_hot else 0, 0, n, memory_count)
            + body)


def _pcp2(cue: dict, is_hot: bool) -> bytes:
    text = (cue.get("comment") or "")
    # UTF-16 BIG-endian, terminated, and the length counts BYTES including the
    # terminator. ANLZ has no 4-byte alignment, so nothing is padded.
    blob = text.encode("utf-16-be") + b"\x00\x00"
    return (b"PCP2" + struct.pack(">II", 16, 88 + len(blob))
            + struct.pack(">I", cue["slot"])
            + b"\x01\x00\x03\xe8"
            + struct.pack(">II", cue["in_msec"], NO_LOOP)
            + b"\x00\x01" + b"\x00" * 6
            + struct.pack(">HH", 0, 0)
            + struct.pack(">I", len(blob)) + blob
            + (HOT_COLOUR if is_hot else MEMORY_COLOUR)
            + b"\x00" * 40)


def build_pco2(cues: list[dict], is_hot: bool) -> bytes:
    body = b"".join(_pcp2(c, is_hot) for c in cues)
    return (b"PCO2" + struct.pack(">II", 20, 20 + len(body))
            + struct.pack(">IHH", 1 if is_hot else 0, len(cues), 0)
            + body)


def containers_for(rows: list[dict]) -> dict[str, dict[tuple[str, int], bytes]]:
    """The six containers a track needs, keyed by file then (fourcc, list_type).

    The legacy pair is split across the two files by design — slots A to C in
    .DAT, D to H in .EXT — and the memory list in .EXT is deliberately empty
    because it lives in .DAT. The extended pair carries all eight.
    """
    hot, mem = prepare(rows)
    dat_hot = [c for c in hot if c["slot"] <= 3]
    ext_hot = [c for c in hot if c["slot"] >= 4]
    return {
        "DAT": {("PCOB", 1): build_pcob(dat_hot, True),
                ("PCOB", 0): build_pcob(mem, False)},
        "EXT": {("PCOB", 1): build_pcob(ext_hot, True),
                ("PCOB", 0): build_pcob([], False),
                ("PCO2", 1): build_pco2(hot, True),
                ("PCO2", 0): build_pco2(mem, False)},
    }


def write_into(data: bytes, containers: dict[tuple[str, int], bytes]) -> bytes:
    """Splice built containers into a file, leaving every other tag alone."""
    where = cue_container_indexes(data)
    return replace_tags(data, {where[k]: v for k, v in containers.items()
                               if k in where})
