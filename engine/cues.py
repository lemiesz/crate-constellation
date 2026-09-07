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
