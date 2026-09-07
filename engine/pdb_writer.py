"""export.pdb / exportExt.pdb writer — builds the legacy Pioneer database the
XDJ-XZ reads, from scratch, using the preserved corpus as structural template.

Strategy (validated against the corpus):
  - file header + every table's "strange" head page are CLONED from the corpus
    (page_index / next_page / table pointers patched) — we never synthesize the
    parts we don't fully understand;
  - tables whose row formats are decoded and byte-round-trip-verified
    (tracks, genres, artists, albums, labels, keys, playlist tree/entries)
    are BUILT from our data;
  - unknown-but-static tables (9,10,11,12,14,15,16,17,18) are cloned verbatim;
  - stale-reference tables (artwork, colors, history) are written EMPTY.

Row serializers reproduce 12,171/12,171 corpus track rows byte-identically
(4-byte row alignment; long strings 4-byte aligned, zero-padded).
"""

import struct
from datetime import date
from pathlib import Path

from .pdb import PAGE, HDR, Pdb, write_string

BUILD_TYPES = {0, 1, 2, 3, 4, 5, 7, 8, 13}
CLONE_TYPES = {6, 16, 17, 18, 19}    # default colors/columns/config/history —
                                     # library-independent, cloned from a FRESH export
# all other types: head-page-only (fresh rekordbox exports give empty tables
# no data page at all — last_page == head, head.next == empty_candidate)


def _aligned_string_blob(strings: list[str], base: int) -> tuple[bytes, list[int]]:
    """Serialize strings sequentially from `base`, 4-aligning long kinds."""
    out = bytearray()
    offs = []
    for s in strings:
        b = write_string(s)
        pos = base + len(out)
        if b[0] in (0x40, 0x90) and pos % 4:
            out += b"\x00" * (4 - pos % 4)
            pos = base + len(out)
        offs.append(pos)
        out += b
    return bytes(out), offs


def serialize_track(t: dict) -> bytes:
    buf = bytearray(0x88)
    struct.pack_into("<HHIIIII", buf, 0, 0x24, 0, 788224,
                     t.get("sample_rate", 44100), 0, t["file_size"], t["master_id"])
    struct.pack_into("<HH", buf, 0x18, 7971, 31905)
    struct.pack_into("<12I", buf, 0x1C, t.get("artwork_id", 0), t["key_id"], 0, t["label_id"], 0,
                     t.get("bitrate", 0), 1, t["tempo"], 0, t["album_id"],
                     t["artist_id"], t["id"])
    struct.pack_into("<6H", buf, 0x4C, 1, 0, t.get("year") or 0, 16,
                     t["duration"], 41)
    buf[0x58] = 0
    buf[0x59] = t.get("rating", 0)
    struct.pack_into("<HH", buf, 0x5A, t.get("filetype", 1), 3)
    today = date.today().isoformat()
    strings = ["", "", "4", "5", "3", "", "ON", "ON", "", "",
               today, "", "", "", t["analyze_path"], today,
               t.get("comment", ""), t["title"], "", t["filename"], t["file_path"]]
    blob, offs = _aligned_string_blob(strings, 0x88)
    struct.pack_into("<21H", buf, 0x5E, *offs)
    return bytes(buf) + blob


def serialize_genre(gid: int, name: str) -> bytes:
    return struct.pack("<I", gid) + write_string(name)


def serialize_label(lid: int, name: str) -> bytes:
    return struct.pack("<I", lid) + write_string(name)


def serialize_key(kid: int, name: str) -> bytes:
    return struct.pack("<II", kid, kid) + write_string(name)


def serialize_artist(aid: int, name: str) -> bytes:
    b = write_string(name)
    if b[0] in (0x40, 0x90):        # long strings 4-align (row base is aligned)
        return struct.pack("<HHI", 0x60, 0, aid) + bytes([3, 12]) + b"\x00\x00" + b
    return struct.pack("<HHI", 0x60, 0, aid) + bytes([3, 10]) + b


def serialize_album(aid: int, artist_id: int, name: str) -> bytes:
    # corpus-uniform subtype 0x80 layout: extra u4, marker 0x03, ofs byte 0x16
    b = write_string(name)
    if b[0] in (0x40, 0x90):        # name base 22 -> pad to 24 for long strings
        return (struct.pack("<HHIII", 0x80, 0, artist_id, aid, 1)
                + struct.pack("<I", 0) + bytes([3, 0x18]) + b"\x00\x00" + b)
    return (struct.pack("<HHIII", 0x80, 0, artist_id, aid, 1)
            + struct.pack("<I", 0) + bytes([3, 0x16]) + b)


def serialize_artwork(aid: int, path: str) -> bytes:
    return struct.pack("<I", aid) + write_string(path)


def serialize_tree(pid: int, parent: int, sort: int, is_folder: bool, name: str) -> bytes:
    return (struct.pack("<5I", parent, 0, sort, pid, 1 if is_folder else 0)
            + write_string(name))


def serialize_entry(idx: int, track_id: int, playlist_id: int) -> bytes:
    return struct.pack("<3I", idx, track_id, playlist_id)


def serialize_matching(owner: int, partner: int, ms: int) -> bytes:
    return struct.pack("<6I", ms & 0xFFFFFFFF, ms >> 32, partner, owner, 0, 3)


# ------------------------------------------------------------- page builder --
def _alloc(typ: int, rlen: int) -> int:
    """rekordbox's per-row heap allocation (measured on a deck-verified export):
    tracks ceil8(len+48); artists/albums ceil4(len+6); others ceil4(len)."""
    if typ == 0:
        return (rlen + 48 + 7) & ~7
    if typ in (2, 3):
        return (rlen + 6 + 3) & ~3
    return (rlen + 3) & ~3


def build_page(page_index: int, typ: int, next_page: int, rows: list[bytes],
               generation: int = 1, ghost: bool = False) -> bytes:
    """One data page, rekordbox-emulating (all rules measured, none assumed):
    ghost=True duplicates every row (ghost copies first, presence on the live
    half only — rekordbox's write-then-rewrite churn for tree/entries);
    u16@0x19 = 0x20*present; written mask = last slot only; @0x20 = 1;
    @0x22 = slots-1; flags 0x34 iff ghosts else 0x24."""
    live = list(rows)
    slots = ([*live, *live] if ghost else live)      # ghosts occupy first half
    n = len(slots)
    present_lo = len(live)
    buf = bytearray(PAGE)
    struct.pack_into("<I", buf, 0x04, page_index)
    struct.pack_into("<I", buf, 0x08, typ)
    struct.pack_into("<I", buf, 0x0C, next_page)
    struct.pack_into("<I", buf, 0x10, generation)
    buf[0x18] = n & 0xFF
    struct.pack_into("<H", buf, 0x19, (0x20 * present_lo) | (1 if n > 0xFF else 0))
    buf[0x1B] = 0x34 if ghost else 0x24
    offs, pos = [], 0
    for slot, r in enumerate(slots):
        offs.append(pos)
        buf[HDR + pos:HDR + pos + len(r)] = r
        if typ in (0, 2, 3):        # subtype'd rows: index_shift = 0x20*slot
            struct.pack_into("<H", buf, HDR + pos + 2, (slot % 0x2000) * 0x20)
        pos += _alloc(typ, len(r))
    used = pos
    fullg, k = n // 16, n % 16
    index_bytes = 36 * fullg + (4 + 2 * k if k else 0)
    struct.pack_into("<H", buf, 0x1C, PAGE - HDR - used - index_bytes)
    struct.pack_into("<H", buf, 0x1E, used)
    struct.pack_into("<H", buf, 0x20, 1 if n else 0)
    struct.pack_into("<H", buf, 0x22, n - 1 if n else 0)
    ghost_cut = n - len(live)                        # first live slot index
    for g in range((n + 15) // 16):
        base = PAGE - g * 36
        pf = wf = 0
        for i in range(min(16, n - g * 16)):
            slot = g * 16 + i
            struct.pack_into("<H", buf, base - 6 - 2 * i, offs[slot])
            if slot >= ghost_cut:
                pf |= 1 << i
            if slot == n - 1:
                wf |= 1 << i
        struct.pack_into("<H", buf, base - 4, pf)
        struct.pack_into("<H", buf, base - 2, wf)
    return bytes(buf)


def page_capacity_split(typ: int, rows: list[bytes], ghost: bool) -> list[list[bytes]]:
    """Split rows into pages honoring the measured alloc + ghost duplication."""
    mult = 2 if ghost else 1
    pages, cur, pos = [], [], 0
    for r in rows:
        a = _alloc(typ, len(r)) * mult
        n = (len(cur) + 1) * mult
        index_bytes = 36 * (n // 16) + (4 + 2 * (n % 16) if n % 16 else 0)
        if cur and HDR + pos + a + index_bytes + 4 > PAGE:
            pages.append(cur)
            cur, pos = [], 0
        cur.append(r)
        pos += a
    if cur:
        pages.append(cur)
    return pages


# ------------------------------------------------------------------ writer --
def build_pdb(template_path: Path, datasets: dict[int, list[bytes]]) -> bytes:
    """Build export.pdb emulating rekordbox's fresh-export conventions, with
    the deck-verified template supplying head pages, per-type generations,
    file sequence, and empty-candidate ordering."""
    tpl = Pdb(Path(template_path).read_bytes())
    td = tpl.data
    n_tables = tpl.num_tables

    head_page = {t["type"]: 1 + i * 2 for i, t in enumerate(tpl.tables)}
    first_data = {t["type"]: 2 + i * 2 for i, t in enumerate(tpl.tables)}
    next_free = 1 + 2 * n_tables
    tpl_gen = {}
    for t in tpl.tables:
        ch = tpl.chain(t["type"])
        tpl_gen[t["type"]] = (struct.unpack_from("<I", td, ch[1] * PAGE + 0x10)[0]
                              if len(ch) > 1 else 1)

    GHOST_TYPES = {7, 8}
    table_pages: dict[int, list[bytes]] = {}
    table_page_idx: dict[int, list[int]] = {}
    for t in tpl.tables:
        typ = t["type"]
        rows_in = datasets.get(typ, []) if typ in BUILD_TYPES else None
        if typ in BUILD_TYPES and rows_in:
            split = page_capacity_split(typ, rows_in, typ in GHOST_TYPES)
            idxs = [first_data[typ]]
            for _ in range(len(split) - 1):
                idxs.append(next_free)
                next_free += 1
            pages = [build_page(idxs[j], typ,
                                idxs[j + 1] if j + 1 < len(idxs) else 0, rows,
                                generation=tpl_gen[typ],
                                ghost=typ in GHOST_TYPES)
                     for j, rows in enumerate(split)]
            table_pages[typ] = pages
            table_page_idx[typ] = idxs
        elif typ in CLONE_TYPES and tpl.chain(typ)[1:]:
            chain = tpl.chain(typ)[1:]
            idxs = [first_data[typ]]
            for _ in range(len(chain) - 1):
                idxs.append(next_free)
                next_free += 1
            pages = []
            for j, cpg in enumerate(chain):
                raw = bytearray(td[cpg * PAGE:(cpg + 1) * PAGE])
                struct.pack_into("<I", raw, 0x04, idxs[j])
                struct.pack_into("<I", raw, 0x0C,
                                 idxs[j + 1] if j + 1 < len(idxs) else 0)
                pages.append(bytes(raw))
            table_pages[typ] = pages
            table_page_idx[typ] = idxs
        else:
            table_pages[typ] = []                    # head-only (empty table)
            table_page_idx[typ] = []

    total_written = next_free
    # empty candidates: head-only tables use their own (zeroed, in-file) even
    # page; with-data tables get beyond-EOF pages ordered like the template
    candidates = {}
    withdata = [t["type"] for t in tpl.tables if table_page_idx[t["type"]]]
    tpl_order = sorted(withdata, key=lambda ty: next(
        (x["empty_candidate"] for x in tpl.tables if x["type"] == ty), 1 << 30))
    for rank, ty in enumerate(tpl_order):
        candidates[ty] = total_written + rank
    for t in tpl.tables:
        if t["type"] not in candidates:
            candidates[t["type"]] = first_data[t["type"]]
    next_unused = total_written + len(withdata)

    for typ, pages in table_pages.items():
        if pages:
            last = bytearray(pages[-1])
            struct.pack_into("<I", last, 0x0C, candidates[typ])
            pages[-1] = bytes(last)

    out = bytearray(total_written * PAGE)
    out[0:PAGE] = td[0:PAGE]
    struct.pack_into("<I", out, 0x0C, next_unused)
    struct.pack_into("<I", out, 0x14, tpl.sequence)
    for i, t in enumerate(tpl.tables):
        typ = t["type"]
        last = table_page_idx[typ][-1] if table_page_idx[typ] else head_page[typ]
        struct.pack_into("<4I", out, 28 + i * 16, typ, candidates[typ],
                         head_page[typ], last)

    for i, t in enumerate(tpl.tables):
        typ = t["type"]
        hp = head_page[typ]
        chp = tpl.chain(typ)[0]
        out[hp * PAGE:(hp + 1) * PAGE] = td[chp * PAGE:(chp + 1) * PAGE]
        struct.pack_into("<I", out, hp * PAGE + 0x04, hp)
        nxt = table_page_idx[typ][0] if table_page_idx[typ] else candidates[typ]
        struct.pack_into("<I", out, hp * PAGE + 0x0C, nxt)

    for typ, pages in table_pages.items():
        for idx, pg in zip(table_page_idx[typ], pages):
            out[idx * PAGE:(idx + 1) * PAGE] = pg
    return bytes(out)
