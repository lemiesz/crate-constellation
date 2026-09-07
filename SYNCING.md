# USB Syncing — how it works and how to maintain it

Deck-verified 2026-09-06: a stick built entirely by this tool (no rekordbox)
shows the full library view — playlists, browse, search — on the XDJ-XZ.

## Using it

1. **USB screen → Sync wizard.** Pick the target volume, check playlists
   (checked = what the stick will contain; ✓ = already fully on that stick,
   `n/m` = partial). Capacity math blocks selections that don't fit.
2. **Sync.** Progress is byte-accurate; already-copied audio is skipped, so
   re-syncs only pay for what changed. Step timings land in the job log.
3. **Validate before walking to the deck:** open rekordbox and check the
   stick in its device panel. rekordbox displaying the device library is
   Pioneer's own parser accepting our bytes — the cheap oracle. Then the deck.
4. Eject cleanly. If a deck ever claims "corrupted" for a stick that
   validates, power-cycle the deck (mains out ~30s) — it caches per-device
   state (learned the hard way, July 2026).

## What a sync writes

| Artifact | How |
|---|---|
| `/Contents/<Artist>/<file>` | audio, 8MB-chunk copies, resume-safe |
| `/PIONEER` or `/.PIONEER` | **FAT32 → plain name; HFS+ → dot-hidden.** Wrong name = deck silently folder-views. DB-internal paths carry the same prefix. |
| `…/rekordbox/export.pdb` | built from scratch by `engine/pdb_writer.py` — the XZ's database |
| `…/rekordbox/exportExt.pdb` | verbatim copy of a known-good fresh export (My Tag defaults; not required for library view but a corrupt one can break the deck) |
| `…/rekordbox/exportLibrary.db` | OneLibrary (CDJ-3000-class players), built locally then copied once — never build SQLite directly on a slow stick |
| `…/USBANLZ/P{xxx}/{hash}/ANLZ0000.*` | waveforms/grids copied from rekordbox's local analysis, **placed at Pioneer's hash-computed path** (players ignore the DB's analyze_path and recompute the hash), PPTH path byte-spliced + null-terminated |
| `…/Artwork/00001/aN.jpg` + `aN_m.jpg` | covers **deck-normalized**: square center-crop, 80px + 240px `_m` variant, baseline JPEG — exactly rekordbox's own cache sizes. Raw embedded art at arbitrary sizes silently renders as NO art on the deck. Served from `art-cache/<sha1>_80/_240.jpg` (precomputed, deduped by content). |
| `…/rekordbox/crate-sync.json` | sync manifest (playlists, counts, timestamp) — powers Drive settings repair/re-sync without re-picking playlists |
| settings, `extracted/gcred.dat` | cloned from the reference export |

## Drive settings (USB screen → Drive settings…)

Per-stick maintenance without the wizard:

- **Re-sync artwork & databases** — re-runs the stick's own manifest: audio
  already present is skipped, artwork/ANLZ/DBs rebuilt. Use after an artwork
  fix or a suspect eject. Sticks synced before manifests existed need one
  normal wizard sync first (the wizard pre-checks fully-synced playlists, so
  it's one click).
- **Check & heal library** — the same idempotent heal pass as the Sync view,
  plus live art-cache coverage.

Finished syncs can be dismissed from the sync drawer (✕ on the job).

## Why the deck accepts our export.pdb

The writer is **byte-identical** (41/41 pages) to a deck-verified fresh
rekordbox export given the same content. That parity is the maintenance
contract: `engine/diff_pdb.py` rebuilds a reference export from parsed
content and byte-compares. If it ever reports diffs after a change, fix the
writer before shipping a stick.

Hard-won rules baked into `engine/pdb_writer.py` (full detail in the
project memory `pdb-writer.md` and `pdb-corpus/` references):

- head ("strange") pages are **cloned from the reference, never synthesized**
- history / columns / menu tables must exist **populated** (cloned) — empty
  history alone makes an XDJ fall back to folder view
- heap alloc slack: tracks `ceil8(len+48)`, artists/albums `ceil4(len+6)`,
  others `ceil4(len)`; rows and long strings 4-byte aligned (also *inside*
  artist/album rows)
- written-mask = last slot only; `u16@0x19 = 0x20 × present`; `@0x20 = 1`;
  `@0x22 = slots−1`; `index_shift = 0x20 × page-relative slot` (tracks,
  artists, albums)
- playlist tree + entry rows are written **twice** (ghost copies, presence
  on the live half, flags `0x34`) — rekordbox's churn, emulated
- every table's `last_page.next_page == empty_candidate`; head-only tables
  point at their own zeroed even page

## The corpus (do not delete)

`pdb-corpus/` holds the irreplaceable references:

- `deck-verified/` — fresh rekordbox export **confirmed working on the XZ**;
  the writer's template (head pages, generations, exportExt) and diff target
- `rekordbox/` — the original 12k-track incremental export (regression corpus)
- `fresh-export/`, `attempt-*/` — earlier references and rejected attempts

If rekordbox ever changes the format (new major version), make a fresh
one-playlist export, deck-verify it, drop it in as the new
`deck-verified/`, and re-run `engine/diff_pdb.py` + the regression gate.

## Speed

The stick is the bottleneck: LORD-AI writes at ~15–18 MB/s regardless of
dock (link negotiates 10 Gbit/s; the flash is the limit) → ~1 GB/min. The
job log prints per-step timings and effective MB/s — if a sync is slow,
read the log before optimizing. A SanDisk Extreme Pro-class stick would be
~10× faster. On gig day none of this matters: decks read at USB-2 rates.

## Known gaps (future work, in rough order)

1. **Hot/memory cues on deck** — rekordbox materializes `djmdCue` into the
   stick ANLZ's PCOB/PCO2 tags at export; our sticks currently carry
   waveforms + grids but empty cue slots. Reference bytes for the format
   are in `pdb-corpus/fresh-export/USBANLZ/`.
2. **Playlist folders** — wizard writes a flat playlist list (tree rows
   support folders; wire the library's folder hierarchy through).
3. **My Tags on deck** — exportExt type-3 row format is documented
   (fragmede FORMAT.md); write our 4 banks instead of the default set.
4. **Matching** — exportExt type-0 rows (24 bytes, format known and
   deck-verified via the old patch scripts) from our similarity engine.
5. FAT32 formatting from the wizard, incremental (non-wipe) DB updates.
   (Artwork: done — deck-normalized 80/240 variants since 2026-09-07.)

## External references

- `PIONEER.md` (AnnoyingTechnology / open-sustain) — hardware-derived spec:
  ANLZ path hash, PPTH null terminator, header formulas. XZ-verified.
- fragmede/rekordbox-pdb FORMAT.md — write-path rules, slot-count encoding.
- Deep Symmetry djl-analysis — the foundational format documentation.
- Holzhaus/rekordcrate PR #259, acrilique/pino — CDJ-350-verified writer.
