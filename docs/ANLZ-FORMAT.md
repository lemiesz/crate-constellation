# ANLZ analysis files: format notes

Everything here was verified against rekordbox-written files on this machine
unless a line says otherwise. All integers are **big-endian**, which is the
opposite of the PDB databases.

Reference writer that reproduced rekordbox byte-for-byte across 4 tracks and
24 cue containers: see `engine/cues.py`.

## File shape

```
PMAI  fourcc[4] | len_header u4 | len_file u4 | ...
then back-to-back tags:
      fourcc[4] | len_header u4 | len_tag u4 | payload
```

`len_tag` covers the **whole tag including its 12-byte header**. There are no
internal absolute offsets anywhere in the file, so splicing a tag only
requires rewriting `PMAI.len_file` at offset 8.

**Never locate a tag by searching for its four-character code.** Every file
begins with a UTF-16 path string that contains false matches; that mistake
convinced this project twice that its cue containers were populated when they
were empty. Walk the chain by following `len_tag`. `engine/anlz.py` does this.

Tag order, which rekordbox keeps stable:

| File | Tags |
|---|---|
| `.DAT` | `PPTH PVBR PQTZ PWAV PWV2 PCOB PCOB` — cue tags last |
| `.EXT` | `PPTH PWV3 PCOB PCOB PCO2 PCO2 PQT2 PWV5 PWV4 [PVB2] PSSI` — cue tags mid-file, tail shifts |
| `.2EX` | no cue tags at all |

`pyrekordbox` cannot parse this library's files (`maximum recursion depth
exceeded`) and its builder is broken for them. Do not reach for it.

## Where cues live, and where they do not

rekordbox keeps cues in its own database (`djmdCue`) and materializes them
into the analysis files **only when it exports**. Scanning all 63,244 files in
`~/Library/Pioneer/rekordbox/share/PIONEER/USBANLZ/`: not one has a populated
cue container. Populated ones exist only in exports, e.g.
`pdb-corpus/fresh-export/USBANLZ/P012/0002C20C/`.

So a device built by copying the local analysis files carries **no cues**,
however many the library holds. Writing them is not optional polish; it is
the only way they ever reach a deck.

## Six containers per track

| File | Containers, in order |
|---|---|
| `.DAT` | `PCOB` hot **slots 1–3 only**, `PCOB` memory **all** |
| `.EXT` | `PCOB` hot **slots 4–8**, `PCOB` memory **empty on purpose**, `PCO2` hot **all 8**, `PCO2` memory **all** |

Hot (`list_type=1`) always precedes memory (`list_type=0`). The 3/5 split is
the format's design, matching Deep Symmetry's dbserver documentation where the
legacy list carries A–C and the nxs2 list carries A–H. The empty memory
container in `.EXT` is deliberate: that list lives in `.DAT`. Write all six.

A reader takes either the extended list or the legacy list and never merges
them, so each must be self-sufficient.

**A track missing its `.EXT` silently loses hot cues D–H.**

## PCOB — legacy container, `len_header` 24, `len_tag` = 24 + 56×n

| Off | Size | Field |
|---|---|---|
| 12 | u4 | `list_type`: 0 memory, 1 hot |
| 16 | u2 | zero |
| 18 | u2 | `num_cues` |
| 20 | u4 | `memory_count` |

`memory_count` is `num_cues − 1` as a signed 32-bit value for memory lists, so
zero cues gives `0xFFFFFFFF`; for hot lists it is **always** `0xFFFFFFFF`.

Empty, exactly 24 bytes:

```
hot:    50 43 4f 42 00 00 00 18 00 00 00 18 00 00 00 01 00 00 00 00 ff ff ff ff
memory: 50 43 4f 42 00 00 00 18 00 00 00 18 00 00 00 00 00 00 00 00 ff ff ff ff
```

## PCPT — legacy entry, fixed 56 bytes

| Off | Size | Field |
|---|---|---|
| 4 | u4 | `len_header` = 28 |
| 8 | u4 | `len_entry` = 56 |
| 12 | u4 | `hot_cue`: 0 memory, 1–8 = A–H |
| 16 | u4 | `status`: 0 normal (4 = active loop, unconfirmed) |
| 20 | u4 | constant `0x00010000` |
| 24 | u2 | `order_first` |
| 26 | u2 | `order_last` |
| 28 | u1 | `cue_type`: 1 point (2 loop, unconfirmed) |
| 29 | 3 | constant `00 03 e8` |
| 32 | u4 | `time`, milliseconds |
| 36 | u4 | `loop_time`, **`0xFFFFFFFF`** when not a loop, not zero |
| 40 | 16 | zero |

Order chain, for entry `i` of `n`: memory lists use `order_first = i−1` with
`0xFFFF` for the first and `order_last = i+1` with `0xFFFF` for the last. Hot
lists set **both to `0xFFFF`** and carry no chain.

## PCO2 — extended container, `len_header` 20

Field order differs from PCOB: the count comes before the padding, and there
is **no `memory_count`**.

| Off | Size | Field |
|---|---|---|
| 12 | u4 | `list_type` |
| 16 | u2 | `num_cues` |
| 18 | u2 | zero |

Empty, exactly 20 bytes:

```
hot:    50 43 4f 32 00 00 00 14 00 00 00 14 00 00 00 01 00 00 00 00
memory: 50 43 4f 32 00 00 00 14 00 00 00 14 00 00 00 00 00 00 00 00
```

## PCP2 — extended entry, variable, `len_entry` = 88 + `len_comment`

| Off | Size | Field |
|---|---|---|
| 4 | u4 | `len_header` = 16 |
| 8 | u4 | `len_entry` = 88 + `len_comment` |
| 12 | u4 | `hot_cue` |
| 16 | u1 | `cue_type` |
| 17 | 3 | constant `00 03 e8` |
| 20 | u4 | `time` |
| 24 | u4 | `loop_time` |
| 28 | u1 | `color_id`, small enum, 0 everywhere here |
| 29 | u1 | constant `0x01` |
| 30 | 6 | zero |
| 36 | u2 | loop numerator, 0 for points |
| 38 | u2 | loop denominator, 0 for points |
| 40 | u4 | `len_comment` |
| 44 | lc | comment |
| 44+lc | 4 | colour |
| 48+lc | 40 | zero |

**Comment**: UTF-16 **big-endian**, no BOM, terminated by `00 00`.
`len_comment` counts **bytes and includes the terminator**, so it is
`2 × (characters + 1)`. Verified: `"Cue 8"` gives 12 and `len_entry` 100.

**There is no alignment padding.** ANLZ tags are not 4-byte aligned; `PPTH`
routinely ends at an odd multiple of 2. Do not carry the PDB writer's
alignment rules across, and an 8-character comment giving `len_entry` 106 is
correct as it stands.

**Colour**: hot cues `00 1a ff 00`, memory cues `00 00 00 00`. That is
rekordbox's default green, `1a ff 00`, not `00 ff 00`. beat-link warns about
it, and beat-link is wrong; do not "correct" it.

## Ordering

Entries in **every** container run **descending by time**, latest first,
confirmed across all populated containers found. The memory order chain then
follows that same array order.

## Mapping rekordbox's `djmdCue`

```
Kind 0             -> memory cue, hot_cue = 0
Kind 1, 2, 3       -> hot slots 1, 2, 3   (A, B, C)
Kind 5, 6, 7, 8, 9 -> hot slots 4, 5, 6, 7, 8 (D, E, F, G, H)

KIND_TO_HOT = {1:1, 2:2, 3:3, 5:4, 6:5, 7:6, 8:7, 9:8}
```

**Kind 4 is never a hot-cue slot.** Zero rows carry it across 203,066 cues
including soft-deleted ones; pyrekordbox documents it as `Loop`. The mapping
was confirmed by matching one track's database rows to the hot-cue numbers in
its own exported file.

Position is `InMsec`, **milliseconds, never frames**. `InFrame` is a 150 fps
rendering the format does not use.

## Hazards in this library

- **Duplicate rows**: 14,171 colliding `(ContentID, Kind)` pairs across 1,849
  tracks; the worst track has 173 hot-cue rows and 1,099 memory-cue rows.
  Dedupe by kind and clamp to eight slots before writing or the format
  overflows.
- Mirroring `djmdCue` into `library.db` keyed on `(track, kind, position)`
  looks unique and is not: it silently discards 20,000 of 201,794 cues.
- Growth: roughly +616 bytes per `.DAT` and +1880 per `.EXT` for a track with
  eight hot and eight memory cues, about 40 MB across 16,177 tracks.

## Not confirmed

1. **All loop encoding.** No loop exists anywhere on this machine and this
   library has none (`OutMsec` is −1 and `BeatLoopSize` 0 for every row).
   `status=4`, `cue_type=2`, `loop_time` semantics and the loop
   numerator/denominator are spec-only. **Write points only.**
2. Colour for `ColorTableIndex` other than 0.
3. The shape of a comment-less `PCP2` (`len_entry` would be 88); 4,520 cues
   here have no comment.
4. What deck firmware does if a container is omitted. Nobody has published
   that experiment, and every third-party writer found ships empty cue lists,
   so none exercised it. Reproduce all six rather than betting on one.

## Player behaviour worth remembering

Hardware-attested by the author of the XDJ-XZ-verified exporter, **not
verified here**:

- The player **computes the analysis path itself** from the audio path and
  ignores the one stored in the database: `h = (h*0x5BC9 + c) * 0x93B5 + c`
  over the UTF-16 of the USB-relative path, mod 200003. A wrong audio path
  means no waveforms.
- The history tables must be non-empty or the player will not recognise the
  drive at all.
- `.EXT` is required; `.DAT` is only a fallback when `.EXT` is absent.
  `exportExt.pdb` itself is optional.
- **`PWV3`**, the mono detail waveform, drives both needle search and the
  jogwheel display, not `PWV4` or `PWV5`.
