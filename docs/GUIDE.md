# Crate Constellation: a guide for new people

Crate Constellation is a single macOS app for a DJ library. It reads your rekordbox
library into a database of its own (`library.db`). From there you can search, tag,
browse a sound map, and write **XDJ-XZ-ready USB sticks without rekordbox**.

This guide covers getting it running, finding your way around each screen, and the
handful of rules that keep your drives safe. For how sync works internally, read
[SYNCING.md](../SYNCING.md). For the analysis-file format, read
[ANLZ-FORMAT.md](ANLZ-FORMAT.md).

---

## 1. Setup

You need macOS, Python 3.14, Node 20+, and rekordbox 6 or 7 with a library in it.

```sh
git clone <this repo> ~/Music/crate-constellation
cd ~/Music/crate-constellation

python3.14 -m venv .venv
./.venv/bin/pip install -r requirements.txt
(cd app && npm install)

./run.sh import     # seed library.db from rekordbox's master.db (read-only, ~6 s)
./run.sh build      # build the frontend
./run.sh            # serve app + API at http://localhost:8765
```

Open <http://localhost:8765>.

**Importing is always safe.** The importer opens rekordbox's `master.db` read-only, so it
can run while rekordbox is open. Re-run `./run.sh import` whenever you change things in
rekordbox. The Sync screen also has a guarded "Re-import" button that shows which of
your local edits would be overwritten first.

### Optional data from `library-management`

Two features read data produced by the companion analysis pipeline in
`~/Music/library-management` (not part of this repo):

| Feature | Needs | Without it |
|---|---|---|
| "Sounds like" sidebar | `features_v2.sqlite` (discogs-effnet embeddings) | sidebar is empty |
| Constellation map | `viz_arrays.npz` + `viz_meta.csv` → `engine/import_viz.py` | map is empty |

### The PDB corpus (needed to write sticks)

The USB writer uses a **rekordbox-written, deck-verified export as its structural
template**. It clones the file header and each table's head page from that export. The
template is personal library data, so it is **not in git**: `pdb-corpus/` is gitignored
and lives only on the machine that built it.

To write sticks on a new machine, build your own template:

1. In rekordbox, export **one small playlist** to a freshly formatted stick.
2. Play a track from it on your deck to confirm the deck accepts it.
3. Copy the stick's `PIONEER/rekordbox/` (`.PIONEER/` on HFS+) to
   `pdb-corpus/deck-verified/rekordbox/`.

Library, Constellation, Energy, Backups and the USB *explorer* all work without a
corpus. Only writing sticks needs one.

---

## 2. A tour of the screens

The rail on the left switches screens. The right-hand pane always shows the selected
track.

### Library

![Library](screenshots/01-library.png)

Your whole collection, with the rekordbox playlist tree on the left.

- **Search is fuzzy:** `jayz`, `jay-z` and `jay z` all match. It searches title, artist,
  album, label, comment and filename together.
- **Double-click a row to play it.** The waveform is rekordbox's own preview waveform,
  read from its analysis files. Click the waveform to seek.
- **Right-click** a row for more actions.

![Library with a track selected](screenshots/02-library-detail.png)

Selecting a track fills the detail pane:

- **Energy 1–10:** click a number to set it.
- **My Tags** in four banks: Genre, Energy, Vibe, Label. Edits are journaled and
  mirrored into the comment field as hashtags.
- **Sounds like:** nearest neighbours by audio embedding, with similarity scores.
  Click one to inspect it, double-click to play.

### Constellation

![Constellation](screenshots/03-constellation.png)

Every track as a point on a 2-D sound map. Colours are genre families; shades within a
colour are subgenres. Scroll to zoom, drag to pan, click to select (it draws edges to
the selected track's neighbours), double-click to play.

### Energy & Tags

![Energy and tags](screenshots/04-energy-tags.png)

The E1–E10 histogram and all four tag banks. Click a bar or a tag chip to filter the
library to it. Energy is a *computed, relative* scale owned by one writer; don't
hand-add `warmup`/`peak`-style tags to the Energy bank.

### Sync

![Sync](screenshots/05-sync.png)

- **rekordbox / Traktor / USB cards** show the state of each target.
- **"N · review"** lists local edits since the last import, collapsed to net changes.
- **Heal library** is an idempotent repair pass: integrity check, missing files,
  orphaned links, energy-tag reconcile, comment-hashtag mirror. Running it twice
  fixes nothing the second time.
- **USB sync log** has one row per stick written, with size, time and throughput.

### USB stick

![USB](screenshots/06-usb.png)

With a rekordbox stick mounted, this screen explores it. It copies the stick's
database off first and **never writes to the stick**. It diffs the stick against your
library: what's on it, what's missing, what's orphaned. **Patch matching** pushes the
full Matching (similar-tracks) table onto a stick that rekordbox exported.

**Sync wizard…** builds a stick from scratch:

![Sync wizard](screenshots/08-sync-wizard.png)

1. Pick a target drive and tick the playlists it should contain. Checked means "what
   the stick will hold after the sync".
2. Review, then start. The wizard closes, and a drawer on the right tracks progress,
   so the app stays usable. You can sync several sticks in parallel.

Each sync writes audio, analysis files with cues, `export.pdb` + `exportExt.pdb`,
OneLibrary and player settings. It then re-parses its own output as a verification
gate before calling the job done.

### Backups

![Backups](screenshots/07-backups.png)

Every on-disk snapshot the app knows about: rekordbox `master.db` backups, Traktor
`collection.nml`, and stick backups taken before matching patches. "Reveal" opens one
in Finder.

---

## 3. Rules that keep drives safe

These rules exist because breaking them cost real data. Please follow them.

1. **Drive operations go through the app.** Sync, initialize and matching pushes run
   from the UI or its HTTP endpoints, never from ad-hoc scripts. A one-off script once
   replaced a 19,717-track drive with 7 tracks.
2. **Test on a sacrificial stick first.** Format changes get deck-tested on a throwaway
   stick before they touch a drive you rely on.
3. **Your source-of-truth SSD and its backup drives are not test targets.** The wizard
   offers them because you may choose them deliberately. Automation never should.
4. **New capabilities ship behind a setting, off by default.** With every flag off,
   a sync must produce the same bytes as the last known-good tag
   (`working-2026-09-07`).
5. **Beat grids are constant-tempo.** Tempo + first-beat offset + downbeat phase, never
   a per-beat tempo curve.

### When a stick misbehaves

| Symptom | Cause | Fix |
|---|---|---|
| Deck shows folder view only | DBs unreadable, or `PIONEER` vs `.PIONEER` wrong for the filesystem | HFS+ uses hidden `.PIONEER`, FAT32 uses `PIONEER`. Run `engine/verify_pdb.py` |
| Deck says "library corrupted" but the data checks clean | Deck-side cache | Power-cycle the deck |
| rekordbox crashes within ~25 s whenever the stick is mounted | Stale `sync.temp` markers from an interrupted export | With rekordbox closed, delete `sync.temp` / `sync.devlibplus.temp` / `._*` in `PIONEER/rekordbox/`. Leave the PDBs alone |
| rekordbox says it will "convert the library" | Nothing. It says this for its own exports too | Ignore it |
| Artwork missing on deck | Index-page first-data-page pointer (0x2C) stale | Fixed in the writer; `verify_pdb.py` checks it |

---

## 4. Developing

```sh
./run.sh dev                      # Vite hot reload on :5173, engine on :8765
cd app && npx playwright test     # end-to-end tests against the running engine
```

- The engine always runs with `--reload`, so the code you click is the code on disk.
  Every sync records the build that wrote it (`/api/version`).
- USB tests auto-skip when no stick is mounted.
- Commit each finished feature on its own (code, tests and docs together).

### Where things live

```
engine/
  server.py            FastAPI app, all /api routes
  import_rekordbox.py  master.db → library.db (read-only)
  schema.sql           library.db schema
  usb_sync.py          the sync wizard's job: audio, ANLZ, PDBs, OneLibrary, settings
  usb_init.py          initialize a drive the way rekordbox does
  pdb.py / pdb_writer.py  DeviceSQL (export.pdb) parser and from-scratch writer
  verify_pdb.py        structural checks every written PDB must pass
  anlz.py / cues.py    ANLZ tag walker and cue-container builder
  matching.py / push_matching.py  Matching table (exportExt type 0)
  heal.py              Heal Library + artwork cache
  diff_pdb.py, diff_onelibrary.py, compare_exports.py, dump_device.py  forensics
app/src/               React 19 + TypeScript UI (Vite)
app/tests/             Playwright specs
docs/                  format references and plans
```
