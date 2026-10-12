---
name: device-writer-change
description: Safe workflow for changing anything that writes to a DJ device: the export.pdb/exportExt.pdb writer, OneLibrary, ANLZ/cue files, or the sync job. Use before editing engine/pdb_writer.py, pdb.py, usb_sync.py, usb_init.py, anlz.py, cues.py or matching.py, or when adding a new field to what lands on a stick.
---

# Changing the device writer

This code is hardware-facing and deck-verified. A regression here means a stick that
won't play mid-gig. Follow the gates in order.

## Before editing

1. Read the relevant section of `SYNCING.md` and `docs/ANLZ-FORMAT.md`. The format rules
   there were each paid for with a failed deck test.
2. Make sure the corpus template exists: `pdb-corpus/deck-verified/rekordbox/export.pdb`.
   It is gitignored and local-only; without it nothing here can be validated.
3. Record the baseline:

   ```sh
   ./.venv/bin/python -m engine.diff_pdb pdb-corpus/deck-verified/rekordbox/export.pdb
   ```

   **Expected today: 40/41 pages identical.** The one difference is page 0's format
   version (ref 0x4, ours 0x5), written on purpose: version 5 is what stops rekordbox's
   conversion prompt (commit `529b48a`). Any *other* differing page is a regression.

## While editing

- **New behaviour goes behind a setting, default off** (`library.db.meta`, read at sync
  time, recorded in the stick's sync manifest). With every flag off, output must be
  byte-identical to tag `working-2026-09-07`. Write that equivalence check first.
- Never synthesize "strange" head pages (flags 0x64) or the file header: clone from the
  template and patch them, including the **0x2C first-data-page pointer**.
- PDB is little-endian with 4-byte-aligned rows. ANLZ is big-endian with no alignment.
  Walk ANLZ tags by `len_tag`, never by searching for a fourcc.
- Beat grids stay constant-tempo (tempo + offset + phase). No per-beat tempo.

## Gates (all must pass, in order)

1. `diff_pdb` baseline unchanged (40/41, as above), unless the change is meant to
   alter bytes, in which case explain every new differing byte.
2. Cue/ANLZ changes: rebuild the containers for the four `pdb-corpus/fresh-export` tracks
   with `cues.containers_for` and byte-compare them against rekordbox's files (24/24 today;
   see commit `358b08c`). There is no standalone script for this yet, so write a read-only
   one in the scratchpad.
3. Run a real sync **through the app** (wizard or `/api/usb/wizard/*`) to the sacrificial
   stick (LORD-AI). Never use a scratch script, and never T7 or drcomputer-backup(s).
4. `./.venv/bin/python -m engine.verify_pdb /Volumes/LORD-AI` reports clean.
5. `cd app && npx playwright test` is green.
6. **Deck test.** Ask the user to load the stick on the XZ and check the specific feature.
   Until they confirm, report it as "awaiting deck test", not done.

## After

- Update `SYNCING.md` / `docs/ANLZ-FORMAT.md` with any format fact you learned.
- Commit this one feature (code + tests + docs), saying what changed for the user and why.
