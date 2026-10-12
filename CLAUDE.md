# CLAUDE.md

Crate Constellation: a macOS DJ-library app. `library.db` is the source of truth, seeded
read-only from rekordbox's `master.db`. Its main job is writing XDJ-XZ sticks from scratch
without rekordbox. Engine is FastAPI (Python 3.14, `.venv`); UI is React 19 + TS (Vite),
served by the engine on :8765.

Read first: `docs/GUIDE.md` (setup + screens), `SYNCING.md` (how sync works and why the deck
accepts it), `docs/ANLZ-FORMAT.md` (analysis files), `docs/OWN-ANALYSIS-PLAN.md` (next big
project). Keep those docs current when you learn something; they are the reference, not
your memory.

## Commands

```sh
./run.sh import      # master.db → library.db, read-only, safe with rekordbox open
./run.sh build       # build app/dist
./run.sh             # engine + built app on :8765 (always --reload)
./run.sh restart     # kill whatever holds :8765, come back up
./run.sh dev         # Vite on :5173 + engine
cd app && npx playwright test          # 51 e2e tests; usb specs skip with no stick
./.venv/bin/python -m engine.verify_pdb /Volumes/<STICK>   # structural PDB checks
./.venv/bin/python -m engine.dump_device /Volumes/<STICK>  # full device state, for diffing
```

Playwright's bundled browser may not be installed; scripts can use
`chromium.launch({ channel: "chrome" })`.

## How to work here

- **Drive operations go through the app's endpoints**: `/api/usb/wizard/*`,
  `/api/usb/initialize`, `/api/usb/patch-matching`. Do it exactly as the UI does, or ask the
  user to run it from the wizard. Scratch scripts may *inspect* output, never *perform* a
  sync. A one-off script once replaced a 19,717-track drive with 7 tracks.
- **Never touch `/Volumes/T7`, `/Volumes/drcomputer-backup(s)` unprompted**: no tests,
  benchmarks, cleanup or forensics. T7 is the source-of-truth SSD; the others hold its
  backups. The wizard still offers them on purpose (user's choice); don't add them to
  `EXCLUDE_VOLUMES`.
- **Sacrificial stick first.** Format changes are deck-tested on a throwaway stick (LORD-AI)
  before any drive that matters. Only the deck is ground truth: our parser, `verify_pdb`
  and `diff_pdb` once all passed a file the deck read wrong.
- **New capabilities ship behind a setting, default off.** Flags live in `library.db.meta`,
  are read at sync time, and are recorded in the stick's sync manifest. All flags off must
  reproduce the bytes of tag `working-2026-09-07`.
- **Commit each finished feature** (code + tests + docs) on its own; don't batch. Commit
  messages say what changed for the user and why.
- **Verify with curl / Playwright against the running app**, not by reasoning about code.
- **Mass fixers get dry-run-audited on a sample first.** A case-sensitive bug in the
  hashtag mirror once polluted 17.5k comments. Heal steps must be idempotent (a 2nd run
  fixes 0).
- Confirm destructive actions in the page, never with `window.confirm`/`alert`; browser
  dialogs also block automation.

## Domain rules

- **Beat grids are constant-tempo**: tempo + first-beat offset + downbeat phase. Never emit
  a per-beat tempo curve, even when the audio drifts; a low confidence score keeps the
  imported grid instead.
- **Cues mark energy changes** (how Mixed In Key works): drops, builds, breakdowns. Lead
  with explicit energy detectors over self-similarity novelty; emit a per-cue energy.
- **Energy is E1–E10, a computed relative scale with one writer** (`apply_energy_tags.py` in
  `~/Music/library-management`). Nothing else writes the Energy bank. The old
  `warmup/groovy/driving/peak` tags came from a *mood* model and are retired.
- BPM in `DjmdContent` is ×100; the importer divides. Many tracks have NULL Title/Artist;
  the UI falls back to the file basename.
- Search uses `search_blob`: lowercase, diacritics folded, alnum only (`jayz` = `jay-z`).
- Paths to rekordbox must be NFC. NFD paths (what `os.listdir` returns for accented
  names) make rekordbox re-import duplicates.

## rekordbox gotchas

- `pyrekordbox` `add_content()` leaves ~20 columns NULL and reads no tags. NULL `ServiceID`
  makes Lexicon drop the track, so its playlists import empty. Backfill after any batch.
- The rekordbox-database MCP can report success while nothing hits `master.db` (dead
  sqlcipher connection; restart the MCP). Its `create_playlist` never adds the node to
  `masterPlaylists6.xml` (hex IDs), so the playlist is invisible. Always verify writes
  out-of-band.
- pyrekordbox's ANLZ parser blows the recursion limit on this library; use `engine/anlz.py`.
- rekordbox's "library conversion" message is benign (it shows it for its own exports).
  Don't chase it.
- rekordbox crashing ~25 s after mount, in the Exporting thread inside `TE_*` = stale
  `sync.temp` / `sync.devlibplus.temp` markers on the stick. With rekordbox closed, delete
  those and `._*` files, then `dot_clean -m`. Don't touch the PDBs.
- Deck says "corrupted" while the data checks clean → power-cycle the deck.

## Device format (hard-won; details in SYNCING.md / ANLZ-FORMAT.md)

**export.pdb (DeviceSQL, little-endian)**
- File header and every table's "strange" head page (flags 0x64) are **cloned from the
  corpus template** and patched. Never synthesize them.
- Head/index pages carry the table's **first data page at body offset 0x2C**
  (`0x03FFFFFF` = empty). Leaving it stale made artwork vanish on the deck while every
  linear-walk check passed. Players follow the index.
- Page header: `u16@0x19 = 0x20×present_rows | overflow bit`; `u16@0x20` = rows written this
  save; `u16@0x22` = slot count before save (0 when fresh); page `@0x10` = generation (1).
  Flags 0x24 = normal, 0x34 = has deletions.
- Rows and long strings are 4-byte aligned. Short-ASCII strings cap at 126 chars.
- Every table's `last_page.next_page == empty_candidate`, which may lie beyond EOF.
- Empty tables are head-page-only. Colours / columns / menu / history tables must ship
  populated (cloned).
- Database format version 5 is what stops rekordbox's conversion prompt.

**Device layout**
- HFS+ uses hidden **`.PIONEER`**, FAT32 uses `PIONEER`; DB-internal paths carry the dot too.
  Wrong name = deck shows folder view only. Journaled HFS+ is fine.
- Use rekordbox's audio layout (`Contents/<Artist>/<Album>/file`), or rekordbox re-exports
  our tracks.
- The XZ reads **Matching from `exportExt.pdb` table type 0** (24-byte rows, 154 per page),
  not OneLibrary. Legacy track IDs = OneLibrary `content_id`. Appending pages is
  deck-verified; never rewrite the head page.
- `exportLibrary.db` = OneLibrary (SQLCipher, public DLP key). The explorer copies it off
  the stick before reading and never writes.

**ANLZ (`.DAT`/`.EXT`, big-endian)**
- No internal absolute offsets: a splice only rewrites `PMAI.len_file`. **Walk tags by
  `len_tag`; never search for a fourcc** (the UTF-16 path string produces false hits).
- No 4-byte alignment in ANLZ (don't carry the PDB rule over).
- rekordbox keeps cues only in `djmdCue` and materializes them at export. We build all six
  cue containers from the `cues` table. `KIND_TO_HOT = {1:1,2:2,3:3,5:4,6:5,7:6,8:7,9:8}`
  (Kind 4 = loop, never a hot slot). Entries descending by time, position in ms,
  `loop_time = 0xFFFFFFFF` for a point. Dedupe (14k colliding rows) and clamp to 8.

## Data outside this repo

- `pdb-corpus/` is **gitignored and local-only** (personal library exports). It is the
  writer's runtime template (`deck-verified/rekordbox/`) and the parity reference.
  Never delete it. `docs/GUIDE.md` explains how to build one.
- `~/Music/library-management` holds the analysis pipeline: embeddings
  (`features_v2.sqlite`), viz arrays (`viz_arrays.npz`), energy calibration, ingest, and
  stick backups (`stick-backups/`). Its skills live in that repo, not here.
- `library.db`, `art-cache/` are generated and gitignored.

## Status (2026-10-12)

- Shipped: library/edit/constellation/energy/sync/backups screens, from-scratch stick
  writer (deck-accepted), cues, legacy Matching, parallel multi-drive sync, Heal Library,
  re-import review.
- **Awaiting deck test:** artwork fix (0x2C index pointer).
- **Next:** `docs/OWN-ANALYSIS-PLAN.md` Phase 1 (our own constant-tempo grid), behind a flag.
- Known gaps: My Tags on deck (exportExt type 3), FAT32 formatting from the wizard,
  incremental (non-wipe) DB updates.
