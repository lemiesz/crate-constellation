"""export.pdb parser + writer (legacy Pioneer database — what the XDJ-XZ reads).

Format per Deep Symmetry's rekordbox_pdb.ksy + our own deck-verified page code
(push_matching_legacy.py). Discipline: the parser is validated against the
preserved corpus (a rekordbox-written, deck-played 12k-track export.pdb) and
the row serializers must reproduce corpus rows byte-identically before the
writer is trusted to build a stick.
"""

import struct
from pathlib import Path

PAGE = 4096
HDR = 40          # page header size; heap starts here

T_TRACKS, T_GENRES, T_ARTISTS, T_ALBUMS, T_LABELS, T_KEYS, T_COLORS = range(7)
T_PLAYLIST_TREE, T_PLAYLIST_ENTRIES = 7, 8
T_ARTWORK, T_COLUMNS = 13, 16
T_HISTORY_PLAYLISTS, T_HISTORY_ENTRIES, T_HISTORY = 19, 20, 21


# ---------------------------------------------------------------- strings ---
def read_string(buf: bytes, off: int) -> tuple[str, int]:
    """DeviceSQL string at offset -> (text, bytes_consumed)."""
    k = buf[off]
    if k & 1:                                   # short ascii
        n = (k >> 1) - 1
        return buf[off + 1:off + 1 + n].decode("ascii", "replace"), 1 + n
    if k == 0x40:                               # long ascii
        n = struct.unpack_from("<H", buf, off + 1)[0]
        return buf[off + 4:off + n].decode("ascii", "replace"), n
    if k == 0x90:                               # long utf-16le
        n = struct.unpack_from("<H", buf, off + 1)[0]
        return buf[off + 4:off + n].decode("utf-16-le", "replace"), n
    raise ValueError(f"unknown string kind 0x{k:02x} at {off}")


def write_string(text: str) -> bytes:
    try:
        raw = text.encode("ascii")
        if len(raw) <= 126:                     # short ascii (kind byte max 0xFF)
            return bytes([((len(raw) + 1) << 1) | 1]) + raw
        return bytes([0x40]) + struct.pack("<H", len(raw) + 4) + b"\x00" + raw
    except UnicodeEncodeError:
        raw = text.encode("utf-16-le")
        return bytes([0x90]) + struct.pack("<H", len(raw) + 4) + b"\x00" + raw


# ----------------------------------------------------------------- parser ---
class Pdb:
    def __init__(self, data: bytes):
        self.data = data
        (self.len_page, self.num_tables, self.next_unused,
         self.u1, self.sequence) = struct.unpack_from("<5I", data, 4)
        assert self.len_page == PAGE, self.len_page
        self.tables = []
        for t in range(self.num_tables):
            typ, ec, first, last = struct.unpack_from("<4I", data, 28 + t * 16)
            self.tables.append(dict(type=typ, empty_candidate=ec,
                                    first=first, last=last))

    def table(self, typ: int) -> dict | None:
        for t in self.tables:
            if t["type"] == typ:
                return t
        return None

    def chain(self, typ: int) -> list[int]:
        t = self.table(typ)
        if not t:
            return []
        pages, pg, seen = [], t["first"], set()
        while pg and pg not in seen:
            seen.add(pg)
            pages.append(pg)
            if pg == t["last"]:
                break
            pg = struct.unpack_from("<I", self.data, pg * PAGE + 12)[0]
        return pages

    def rows(self, typ: int) -> list[tuple[int, int]]:
        """(abs_row_offset, page_index) for every present row, heap order."""
        out = []
        for pg in self.chain(typ):
            off = pg * PAGE
            flags = self.data[off + 0x1B]
            if flags & 0x40:
                continue
            # slots = u8@0x18 + 0x100 * overflow bit (u16@0x19 low bit);
            # presence decided per-slot by the masks (fragmede FORMAT.md)
            nro = self.data[off + 0x18] + 0x100 * (self.data[off + 0x19] & 1)
            for g in range((nro + 15) // 16):
                base = off + PAGE - g * 36
                pf = struct.unpack_from("<H", self.data, base - 4)[0]
                for i in range(min(16, nro - g * 16)):
                    if (pf >> i) & 1:
                        ro = struct.unpack_from("<H", self.data, base - 6 - 2 * i)[0]
                        out.append((off + HDR + ro, pg))
        return out

    # --- row decoders ---
    def track(self, ro: int) -> dict:
        d = self.data
        f = struct.unpack_from("<HHIIIII", d, ro)          # subtype..u4@0x14
        # u18/u1a are the low and high halves of ONE u32 at 0x18: the
        # rekordbox library id (2090934051 for this library), matching
        # exportLibrary.db content.masterDbId.  Kept split for byte-parity
        # round-tripping and because crate-digger names them separately.
        u18, u1a = struct.unpack_from("<HH", d, ro + 0x18)
        (artwork, key_id, orig_artist, label_id, remixer, bitrate, track_no,
         tempo, genre_id, album_id, artist_id, tid) = struct.unpack_from("<12I", d, ro + 0x1C)
        disc, plays, year, depth, dur, u56 = struct.unpack_from("<6H", d, ro + 0x4C)
        color, rating = d[ro + 0x58], d[ro + 0x59]
        u5a, u5c = struct.unpack_from("<HH", d, ro + 0x5A)
        ofs = struct.unpack_from("<21H", d, ro + 0x5E)
        strs = [read_string(d, ro + o)[0] for o in ofs]
        return dict(subtype=f[0], index_shift=f[1], bitmask=f[2], sample_rate=f[3],
                    composer_id=f[4], file_size=f[5], u14=f[6], u18=u18, u1a=u1a,
                    artwork_id=artwork, key_id=key_id, orig_artist_id=orig_artist,
                    label_id=label_id, remixer_id=remixer, bitrate=bitrate,
                    track_number=track_no, tempo=tempo, genre_id=genre_id,
                    album_id=album_id, artist_id=artist_id, id=tid, disc=disc,
                    plays=plays, year=year, sample_depth=depth, duration=dur,
                    u56=u56, color_id=color, rating=rating, u5a=u5a, u5c=u5c,
                    strings=strs)

    def simple_named(self, typ: int) -> list[dict]:
        """genres / labels: u4 id + string. keys: u4 id, u4 id2, string."""
        out = []
        for ro, _pg in self.rows(typ):
            if typ == T_KEYS:
                i, i2 = struct.unpack_from("<II", self.data, ro)
                out.append(dict(id=i, id2=i2, name=read_string(self.data, ro + 8)[0]))
            else:
                i = struct.unpack_from("<I", self.data, ro)[0]
                out.append(dict(id=i, name=read_string(self.data, ro + 4)[0]))
        return out

    def artists(self) -> list[dict]:
        out = []
        for ro, _pg in self.rows(T_ARTISTS):
            subtype, ishift, aid = struct.unpack_from("<HHI", self.data, ro)
            if subtype & 0x04:
                ofs = struct.unpack_from("<H", self.data, ro + 0x0A)[0]
            else:
                ofs = self.data[ro + 0x09]
            out.append(dict(id=aid, subtype=subtype,
                            name=read_string(self.data, ro + ofs)[0]))
        return out

    def albums(self) -> list[dict]:
        out = []
        for ro, _pg in self.rows(T_ALBUMS):
            subtype, ishift, artist_id, aid, u = struct.unpack_from("<HHIII", self.data, ro)
            if subtype == 0x80:                 # modern layout: extra u4, ofs @0x15
                ofs = self.data[ro + 0x15]
            elif subtype & 0x04:
                ofs = struct.unpack_from("<H", self.data, ro + 0x16)[0]
            else:
                ofs = self.data[ro + 0x11]
            out.append(dict(id=aid, artist_id=artist_id,
                            name=read_string(self.data, ro + ofs)[0]))
        return out

    def playlist_tree(self) -> list[dict]:
        out = []
        for ro, _pg in self.rows(T_PLAYLIST_TREE):
            parent, u, sort, pid, raw_folder = struct.unpack_from("<5I", self.data, ro)
            out.append(dict(id=pid, parent_id=parent, sort=sort,
                            is_folder=raw_folder != 0,
                            name=read_string(self.data, ro + 0x14)[0]))
        return out

    def playlist_entries(self) -> list[dict]:
        out = []
        for ro, _pg in self.rows(T_PLAYLIST_ENTRIES):
            idx, tid, pid = struct.unpack_from("<3I", self.data, ro)
            out.append(dict(entry_index=idx, track_id=tid, playlist_id=pid))
        return out


def load(path: Path) -> Pdb:
    return Pdb(Path(path).read_bytes())
