"""Byte-diff our writer's output against a deck-verified rekordbox export,
given identical logical content. Every differing byte gets classified."""
import struct
import sys
from .pdb import *
from . import pdb_writer as W


def rebuild_from_parsed(p, ro):
    t = p.track(ro)
    buf = bytearray(0x88)
    struct.pack_into("<HHIIIII", buf, 0, t["subtype"], t["index_shift"], t["bitmask"],
                     t["sample_rate"], t["composer_id"], t["file_size"], t["u14"])
    struct.pack_into("<HH", buf, 0x18, t["u18"], t["u1a"])
    struct.pack_into("<12I", buf, 0x1C, t["artwork_id"], t["key_id"], t["orig_artist_id"],
                     t["label_id"], t["remixer_id"], t["bitrate"], t["track_number"],
                     t["tempo"], t["genre_id"], t["album_id"], t["artist_id"], t["id"])
    struct.pack_into("<6H", buf, 0x4C, t["disc"], t["plays"], t["year"],
                     t["sample_depth"], t["duration"], t["u56"])
    buf[0x58] = t["color_id"]; buf[0x59] = t["rating"]
    struct.pack_into("<HH", buf, 0x5A, t["u5a"], t["u5c"])
    blob, offs = W._aligned_string_blob(t["strings"], 0x88)
    struct.pack_into("<21H", buf, 0x5E, *offs)
    return bytes(buf) + blob


def datasets_from(p):
    return {
        0: [rebuild_from_parsed(p, ro) for ro, _ in p.rows(0)],
        1: [W.serialize_genre(g["id"], g["name"]) for g in p.simple_named(T_GENRES)],
        2: [W.serialize_artist(a["id"], a["name"]) for a in p.artists()],
        3: [W.serialize_album(a["id"], a["artist_id"], a["name"]) for a in p.albums()],
        4: [W.serialize_label(g["id"], g["name"]) for g in p.simple_named(T_LABELS)],
        5: [W.serialize_key(g["id"], g["name"]) for g in p.simple_named(T_KEYS)],
        7: [W.serialize_tree(t["id"], t["parent_id"], t["sort"], t["is_folder"], t["name"])
            for t in p.playlist_tree()],
        8: [W.serialize_entry(e["entry_index"], e["track_id"], e["playlist_id"])
            for e in p.playlist_entries()],
    }


FIELDS = [(0x00, 4, "gap"), (0x04, 4, "page_index"), (0x08, 4, "type"),
          (0x0C, 4, "next_page"), (0x10, 4, "generation"), (0x14, 4, "u14"),
          (0x18, 1, "slots_lo"), (0x19, 2, "presence_u16"), (0x1B, 1, "flags"),
          (0x1C, 2, "free"), (0x1E, 2, "used"), (0x20, 2, "written_this_save"),
          (0x22, 2, "prev_slots"), (0x24, 2, "u24"), (0x26, 2, "u26")]


def diff(ref_path, out_path=None):
    p = load(ref_path)
    ref = p.data
    mine = W.build_pdb(ref_path, datasets_from(p))
    if out_path:
        open(out_path, "wb").write(mine)
    print(f"sizes: ref={len(ref)} mine={len(mine)}")
    npages = min(len(ref), len(mine)) // PAGE
    identical = 0
    for pg in range(npages):
        a, b = ref[pg*PAGE:(pg+1)*PAGE], mine[pg*PAGE:(pg+1)*PAGE]
        if a == b:
            identical += 1
            continue
        typ = struct.unpack_from("<I", a, 8)[0]
        hdr_diffs = []
        for off, sz, name in FIELDS:
            av = int.from_bytes(a[off:off+sz], "little")
            bv = int.from_bytes(b[off:off+sz], "little")
            if av != bv:
                hdr_diffs.append(f"{name}: ref={av:#x} mine={bv:#x}")
        body_diff = sum(x != y for x, y in zip(a[40:], b[40:]))
        first_body = next((i+40 for i in range(len(a)-40) if a[i+40] != b[i+40]), None)
        print(f"page {pg:3d} (type {typ:2d}): hdr[{'; '.join(hdr_diffs) or 'same'}] "
              f"body_diff_bytes={body_diff}" +
              (f" first@{first_body:#x} ref={a[first_body:first_body+8].hex()} mine={b[first_body:first_body+8].hex()}" if first_body else ""))
    print(f"\n{identical}/{npages} pages byte-identical")


if __name__ == "__main__":
    diff(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
