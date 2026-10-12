---
name: usb-stick-triage
description: Diagnose a USB stick that a Pioneer deck (XDJ-XZ) or rekordbox rejects or misreads: folder view only, "library corrupted", missing artwork/cues/matching, rekordbox crash loops on mount, or a conversion prompt. Use when the user reports a stick or deck problem, or before and after a sync to see what changed on a drive.
---

# USB stick triage

Every step here is **read-only** until the fix step, and the fix goes through the app.
Never run a sync, initialize or matching push from a scratch script; use the UI or its
endpoints (`/api/usb/wizard/*`, `/api/usb/initialize`, `/api/usb/patch-matching`).

**Stop first if the volume is `T7` or `drcomputer-backup(s)`.** Those are off-limits unless
the user explicitly asked for that drive in this conversation.

## 1. Match the symptom

| Symptom | Likely cause | Go to |
|---|---|---|
| Deck shows **folder view only** | Deck can't open the DBs: wrong `PIONEER`/`.PIONEER` for the filesystem, or a page-header defect | 2, 3 |
| Deck says **"library corrupted"**, but our checks are clean | Deck-side cache | Power-cycle the deck. Don't touch the stick |
| rekordbox says **"device library is corrupted"** | OneLibrary deviates from what rekordbox writes (dangling FK = 0 instead of NULL, unfilled columns) | 4 |
| rekordbox **crashes ~25 s after mount**, Exporting thread in `TE_*` in `~/Library/Logs/DiagnosticReports/rekordbox-*.ips` | Stale `sync.temp` markers resume a broken export | 5 |
| rekordbox announces **"library conversion"** | Benign; it says this for its own exports too | Ignore it |
| **Artwork missing** on deck | Index-page first-data-page pointer at 0x2C | 3 |
| Matching partial / one-way on deck | `exportExt.pdb` type 0 is a stale subset | Sync screen → Patch matching |

## 2. Filesystem and folder name

```sh
diskutil info /Volumes/<STICK> | grep -i personality
ls -la /Volumes/<STICK>
```

HFS+ needs hidden **`.PIONEER`**, FAT32 needs **`PIONEER`**. The DB-internal paths carry the
same prefix. Journaled HFS+ is fine.

## 3. Structural PDB checks

```sh
./.venv/bin/python -m engine.verify_pdb /Volumes/<STICK>
```

This checks page-header row counts, the written-mask, the `last.next_page == empty_candidate`
chain invariant, and the 0x2C index pointer. Every rekordbox-written export passes. Our
parser walking the chain linearly is **not** evidence: players follow the index.

## 4. Compare against a rekordbox-written export

```sh
./.venv/bin/python -m engine.diff_onelibrary /Volumes/<STICK>   # vs the corpus references
./.venv/bin/python -m engine.compare_exports /Volumes/<OURS> <rekordbox-export-dir>
```

When unsure what "right" looks like, have rekordbox export one small playlist to a
sacrificial stick and compare against that. This is how the `.PIONEER` and artwork answers
were found.

## 5. rekordbox crash loop

With rekordbox **closed**:

```sh
cd /Volumes/<STICK>/PIONEER/rekordbox   # or .PIONEER
ls -la sync.temp sync.devlibplus.temp ._* 2>/dev/null
rm -f sync.temp sync.devlibplus.temp ._*
dot_clean -m ..
```

Never delete or edit the PDBs or `playlists3*.sync` here.

## 6. What changed on a drive?

```sh
./.venv/bin/python -m engine.dump_device /Volumes/<STICK> > before.txt
# ...rekordbox opens it / deck plays it / we sync...
./.venv/bin/python -m engine.dump_device /Volumes/<STICK> > after.txt
diff before.txt after.txt
```

## Report

State the symptom, the evidence (command output), the cause, and whether the fix is
confirmed on the deck or only by our checks. Only the deck is ground truth.
