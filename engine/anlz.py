"""Minimal, correct walker for Pioneer ANLZ analysis files.

The format is a header followed by back-to-back tags, each carrying its own
total length, so the only safe way to read it is to follow those lengths.
Searching the bytes for a four-character code finds false hits inside the
UTF-16 path string that every file starts with, which is how an earlier
version of this convinced itself the cue containers were populated.

pyrekordbox's parser raises "maximum recursion depth exceeded" on this
library's files and its builder is broken for them, so we do our own reading
and splice bytes directly.

All integers are big-endian.
"""

import struct
from pathlib import Path

HEADER = b"PMAI"


def tags(data: bytes) -> list[tuple[str, int, int]]:
    """[(fourcc, offset, total_length)] in file order."""
    if data[:4] != HEADER:
        raise ValueError("not an ANLZ file")
    len_header, len_file = struct.unpack_from(">II", data, 4)
    out, off = [], len_header
    end = min(len_file, len(data))
    while off + 12 <= end:
        fourcc = data[off:off + 4]
        if not fourcc.isalnum():
            break
        _lh, lt = struct.unpack_from(">II", data, off + 4)
        if lt < 12 or off + lt > end:
            break
        out.append((fourcc.decode("ascii"), off, lt))
        off += lt
    return out


def tag_bytes(data: bytes, fourcc: str) -> list[bytes]:
    return [data[o:o + n] for f, o, n in tags(data) if f == fourcc]


def cue_counts(data: bytes) -> dict:
    """How many cue entries each container in this file actually holds."""
    out = {}
    for fourcc, off, length in tags(data):
        if fourcc == "PCOB":
            # container: type at +12, then u2 unknown, u2 entry count
            kind = struct.unpack_from(">I", data, off + 12)[0]
            n = struct.unpack_from(">H", data, off + 18)[0]
            out.setdefault("PCOB", []).append((kind, n, length))
        elif fourcc == "PCO2":
            kind = struct.unpack_from(">I", data, off + 12)[0]
            n = struct.unpack_from(">H", data, off + 16)[0]
            out.setdefault("PCO2", []).append((kind, n, length))
    return out


def load(path) -> bytes:
    return Path(path).read_bytes()


def replace_tags(data: bytes, new_by_index: dict[int, bytes]) -> bytes:
    """Rebuild a file with some tags replaced, keyed by position in the chain.

    Tags carry their own length and the file header carries the total, so a
    replacement of a different size means rewriting both. Everything not named
    is copied through untouched, which keeps the waveforms and beat grid
    byte-identical to what rekordbox analysed.
    """
    chain = tags(data)
    len_header = struct.unpack_from(">I", data, 4)[0]
    out = bytearray(data[:len_header])
    for i, (_fourcc, off, length) in enumerate(chain):
        out += new_by_index.get(i, data[off:off + length])
    struct.pack_into(">I", out, 8, len(out))          # PMAI len_file
    return bytes(out)


def cue_container_indexes(data: bytes) -> dict[tuple[str, int], int]:
    """Where each cue container sits in the chain, keyed by (fourcc, kind).

    Every file carries four: the older and newer container formats, each once
    for hot cues (kind 1) and once for memory cues (kind 0).
    """
    out = {}
    for i, (fourcc, off, _length) in enumerate(tags(data)):
        if fourcc in ("PCOB", "PCO2"):
            kind = struct.unpack_from(">I", data, off + 12)[0]
            out[(fourcc, kind)] = i
    return out
