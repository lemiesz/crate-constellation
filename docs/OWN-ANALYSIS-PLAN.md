# Own the analysis: our own grids, waveforms and cue points, without rekordbox

*Status: planned, not started. Written 2026-09-07. The last known-good build before
any of this is tagged `working-2026-09-07`.*

## Context

Crate Constellation writes a complete, deck-accepted USB stick — database, artwork, playlists,
matching, hot cues. One dependency remains, and it is the one that still forces you to keep a
rekordbox playlist: **every beat grid and waveform on a stick is a byte-for-byte copy of a file
rekordbox analysed.**

`engine/usb_sync.py:741` reads each track's ANLZ directory out of
`~/Library/Pioneer/rekordbox/share`, splices in a new path and our cue containers, and writes it to
the stick. `library.db.files.anlz_path` is a pointer into rekordbox's share. We author cue
containers and `PPTH`; `PQTZ` (grid), `PQT2`, `PWAV`, `PWV2`, `PWV3`, `PWV4`, `PWV5`, `PSSI` and
`PVBR` pass through untouched. There is no beatgrid, waveform or segment table in the schema.

So a new track today goes: add to rekordbox → analyse there → Mixed In Key for cues → import. This
plan removes both.

Decisions you made:

- **Everything here is behind a setting, default off.** The app as it stands today —
  rekordbox-sourced grids and waveforms, our cues, artwork, matching — has to keep working
  and stay usable exactly as it is. Each phase adds a switch rather than replacing a code
  path, so with the setting off the sync takes the same route it takes today.
- **Replacing MIK is the priority**, and eventually both.
- **rekordbox-imported analysis must have parity with ours** — both first-class, mixable per track.
- **Track intake in the app**, with a push back to rekordbox so that collection stays a mirror.
- **Back catalogue keeps rekordbox's grids**; ours applies to new and re-analysed tracks.

## The shape of the change

Analysis stops being an opaque file we copy and becomes data we own. ANLZ files become a rendering
of it:

```
audio ──┐
        ├─► analysis tables in library.db ──► ANLZ renderer ──► stick
rekordbox ANLZ ──(parser)──┘                                └──► in-app waveform / cue view
```

Parity falls out of that. Importing from rekordbox means *parsing* its analysis into the same tables
our analyser writes, so one track can carry a rekordbox grid and our cues, or the reverse, and
neither the renderer nor the deck can tell.

### New tables (`engine/schema.sql`)

- `analysis` — `grid_bpm`, `grid_conf`, `first_beat_ms`, `downbeat_phase`, `key`, `key_conf`,
  `peak`, `source` (`rekordbox` | `crate`), `engine_version`, `analyzed_at`.

  A grid is exactly three numbers: tempo, first-beat offset, downbeat phase. That is not a
  simplification made for convenience — it is what the user wants (no dynamic grids, ever) and
  what rekordbox already writes: in a 400-track random sample, **400 of 400 grids carry one
  tempo for the whole track**.

  It is also a strong prior worth exploiting rather than merely respecting. A general beat
  tracker is solving a harder problem than we have, and fitting one tempo across its output
  beats its own raw beats — see the measurements below.

  `grid_bpm` is deliberately not `tracks.bpm`. They agree on 398 of those 400 and diverge exactly
  where `bpm_halftime_fix.py` halved the display value for hip-hop and halftime — grid 140,
  `tracks.bpm` 70. The grid needs the physical tempo; the display BPM stays where it is.
- No per-beat table. **We never emit a variable-tempo grid**, even where the audio would
  support one — the user does not use rekordbox's dynamic grid mode, and a grid whose tempo
  moves makes the deck's sync drift mid-mix. A wrong-but-constant grid is recoverable by ear;
  a drifting one is not. Where a track genuinely does drift (live-played, disco, some
  hip-hop), the answer is a low confidence score and keeping the imported grid, not a
  variable one.
- `waveforms` — `track_id`, `kind`, `blob`, cached at the deck's own resolution so sync never
  recomputes and the in-app player can stop parsing rekordbox files.
- `segments` — structural boundaries with labels, from `PSSI` on import or from our analysis.
- `cues` gains `source` and `confidence`, so MIK's cues, rekordbox's and ours are distinguishable
  and a weak detector can never silently overwrite work you trust.

## What I measured before planning

Everything below is from your library and this machine, not from a paper.

| | |
|---|---|
| Grids that are constant tempo | 400 / 400 sampled |
| `PQTZ` tempo == `tracks.bpm` | 398 / 400 (the 2 are the halftime-halved ones) |
| `PSSI` phrase analysis present | 398 / 400 `.EXT` files — **and not encrypted in these files**, the published XOR mask is not applied |
| MIK cues within 25 ms of a bar line | 66.9 % (4,792 cues, 419 tracks) |
| Best-fit **4-bar** phase captures, of the cues that are on a bar line | **96.1 %** mean, 100 % median, ≥ 0.75 on 95 % of tracks |
| Best-fit 8-bar phase | 89.4 % mean |
| Decode + full-track STFT, one core | 0.6–1.2 s per track |
| Beat This (already installed, CPU) | ~4 s per track |
| **Constant-tempo fit over Beat This's beats vs rekordbox's BPM** | **exact to 0.01 BPM on 9 of 11**, median beat error 16 ms |
| `PWV3` / `PWV5` resolution | 150 columns/second (`stick-snapshots/djm.md` says 100/s — it is wrong) |

Two findings change the design:

**Beat This alone is not a grid.** Out of the box its beats drift — beat-F against rekordbox at
±70 ms averaged 0.79, and its median-interval BPM was within 1.5 % on only 6 of 10 tracks. Fitting
a single constant tempo across all of its beats fixes this completely, recovering rekordbox's BPM
*exactly* on 9 of 11 and leaving 16 ms median beat error. The phase coherence of that fit is a free
and honest confidence score: it flagged both failures (0.77 on an octave error, 0.22 on a track that
genuinely is not constant tempo) while every success scored ≥ 0.87.

**Hypermeter is a much stronger constraint here than the literature suggests.** The Zehren work
treats the 4-bar phase as a filter worth ~8 points of precision; on your library, once a cue is on a
bar line at all, the best-fit 4-bar phase explains 96 % of them. That makes a 4-bar phase gate cheap
and safe — but only if the phase comes from the musical phrase, not from a beat tracker's own
downbeat label, which agreed with rekordbox's bar 1 on just 64 % of tracks.

## Phases

Each ends in its own commit and a measurable gate; nothing reaches a stick before its gate passes.
Everything is driven through the app's endpoints — the only standalone scripts are read-only
evaluators.

### Phase 1 — The grid (2 days)

Everything downstream depends on this, and cues cannot be placed without it.

- `engine/anlz_read.py` — parse `PQTZ` and `PSSI` out of rekordbox's files into `analysis` and
  `segments`, tagged `source='rekordbox'`. Extends the existing `engine/anlz.py` walker. Do not use
  pyrekordbox's parser; it blows the recursion limit on this library.
- `engine/grid.py` — Beat This for beat candidates, then a constant-tempo fit (search BPM over
  ±6 %, circular-mean phase) → `grid_bpm`, `first_beat_ms`, and the fit's phase coherence as
  `grid_conf`. Octave check against `tracks.bpm`, reusing the logic in `bpm_halftime_fix.py`.
- Downbeat phase estimated separately from low-band energy and novelty periodicity, **not** taken
  from Beat This's downbeat labels.
- `tools/eval_grid.py` — read-only, whole-library.

**Gate 1** — against all 19,691 rekordbox grids: BPM exact to 0.05 on ≥ 90 % of tracks, median beat
error ≤ 25 ms, downbeat phase matching rekordbox's bar 1 on ≥ 85 %, and `grid_conf` monotonically
related to error so the fallback below is safe. Tracks under the bar keep rekordbox's grid.

### Phase 2 — Our own cue points (replaces MIK) (4–5 days)

Full design below.

- `engine/cuepoints.py` — feature extraction, novelty, EDM detectors, DP selection.
- `tools/eval_cues.py` — the harness, built *before* the detector.
- UI: cues drawn over the waveform in an analysis panel, ours vs MIK toggled, chosen per playlist.
  You judge it there, not in a log.

**Gate 2** — agreement against MIK at ±1 bar, next to the measured baselines, plus the blind A/B and
first-drop criteria below. Then a deck test: one playlist on LORD-AI with our cues, on the XZ.

### Phase 3 — Re-render rekordbox's analysis (the parity gate) (2 days)

- `engine/anlz_read.py` extended to `PQT2`, `PWAV`, `PWV2`, `PWV3`, `PWV4`, `PWV5`, `PVBR`.
- `engine/anlz_build.py` — build a complete `.DAT`/`.EXT`/`.2EX` from the tables, reusing
  `engine/cues.py` for the six cue containers verbatim.

**Gate 3** — round-trip 500 tracks: parse rekordbox's files into tables, render back, compare
byte-for-byte. Identical, or every difference explained and deliberate. This proves the writer
before any of our own DSP is trusted to it.

Format facts confirmed against your files:

| Tag | header | body |
|---|---|---|
| `PVBR` | 16 | 4 + 400×u32BE seek table. **Mandatory** — without it rekordbox silently ignores grid *and* waveform |
| `PQTZ` | 24 | `beat_count`, then 8 bytes/beat: `beat_number(1–4)`, `BPM×100`, `time_ms` |
| `PQT2` | 56 | first/last beat, `entry_count` (**must be > 0** or rekordbox 6/7 shows a flat grid), then u16BE `time_ms % 1000` per beat |
| `PWAV`/`PWV2` | 20 | 400 / 100 bytes, `(whiteness<<5) \| height(0–31)` |
| `PWV3` | 24 | 1 byte/column at 150 columns/second |
| `PWV5` | 24 | 2 bytes/column, same rate: rgb 3+3+3 bits, height 5 bits |
| `PWV4` | 24 | exactly 1200 columns × 6 bytes |

### Phase 4 — Our own waveforms (2 days)

`engine/waveform.py` — RMS and peak per column, three-band split for colour, then render every tag.
The scaling is **calibrated against the rekordbox corpus**, not guessed: fit the height and colour
mapping to minimise error against the files rekordbox produced for the same audio.

**Gate 4** — column heights correlate ≥ 0.98 with rekordbox's for the same track. Then hardware: a
LORD-AI sync where a dozen tracks carry our ANLZ end-to-end and the rest carry rekordbox's. Check
waveform, grid, needle search and jog on the XZ.

### Phase 5 — Intake and write-back (2–3 days)

- **Add tracks in the app** — pick files or a folder in the sync UI, read tags (including the `TKEY`
  and `Energy N` comment MIK already wrote into your files), create `tracks` and `files` rows, queue
  analysis. This is what actually ends the rekordbox playlist.
- **Push to rekordbox** — opt-in, one button, rekordbox closed, `master.db` backed up first.
  `pyrekordbox.add_content()` for new tracks plus BPM, key and cues, and optionally write our ANLZ
  into rekordbox's share and point `AnalysisDataPath` at it, so rekordbox displays *our* grid and
  never re-analyses. Precedent for writing `master.db` is in `build_matching.py` and `ingest_tags.py`.
- `usb_sync.one_analysis` chooses per track: render from tables when `analysis.source='crate'`, copy
  rekordbox's bytes otherwise. Default stays rekordbox for the back catalogue, as you asked.

## Cue point detection

### What MIK is doing, and what that means for the design

MIK finds the sections of a track and cues their boundaries — drops, builds, breakdowns —
so the thing being detected is a **change in energy**, not novelty in the abstract.

Its own output says so. 880 tracks here carry MIK's energy-labelled cues, and the label is
*per cue*, not per track: the value varies within a track on 845 of those 880, most commonly
by 3 to 4 points. Averaged by position, cue 1 sits lowest at 4.35 and the rest hover around
5.4 to 5.7 — an intro anchor followed by sections that rise and fall. Each cue is marking a
section and carrying that section's energy.

Two consequences:

1. **The explicit energy detectors are the primary signal, not a supplement to the SSM.**
   `drop`, `breakdown` and `riser` below are weighted above the novelty curve deliberately.
   Foote novelty finds *any* boundary; a DJ wants the ones where the energy moved.
2. **Emit an energy value per cue**, computed from the section that follows it on the same
   1-10 scale `energy_score.py` already calibrates. That makes our cues readable the way
   MIK's are, and gives a second thing to compare against those 880 tracks beyond position
   alone — a cue in the right place with the wrong energy label means the section boundary
   was found but the section was misread.

### Candidates

Downbeats only, from the Phase 1 grid — 66.9 % of MIK's cues are already within 25 ms of a bar line
and the rest are unreachable by any bar-quantised system, which caps agreement around 0.85.

The 4-bar phase is chosen by maximising total candidate score over each residue class and applied as
a **soft bonus** (+0.35 at 4 bars, +0.25 more at 8, +0.20 more at 16). Given the 96 % measurement it
could be a hard gate, but a wrong phase estimate would then be catastrophic rather than merely
costly, and the bonus recovers nearly the same behaviour.

### Features (bar resolution, ~192 bars for a 6-minute track)

STFT `n_fft=1024, hop=256`; 64-band mel; **HPSS on the 64-band mel, not the full STFT** — 0.47 s
versus 6.5 s for a feature only ever consumed band-wise. Band energies for kick (20–120 Hz), bass,
mid, hats (6–11 kHz); superflux onset envelopes on three bands as a stand-in for drum transcription;
CQT to CENS chroma; spectral flatness; per-bar crest factor.

Five self-similarity matrices — timbre, harmony, drums, loudness, and a 16-d within-bar rhythm
pattern — each with Foote checkerboard novelty at half-widths 4, 8 and 16 bars. Fifteen curves,
normalised to zero-median/unit-MAD, combined with weights. At bar resolution a whole SSM is 4 ms.

Plus explicit EDM detectors, which matter more than the SSM on this material: `drop` (low-band jump
over 8 bars gated on kick density crossing), `breakdown` (low band falls at least 6 dB for 8 bars or
more), `riser` (rising flatness and highs with the kick gone), and first/last audible bar.

```
S(b) = novelty(b) + 1.5*drop(b) + 1.0*breakdown(b) + 0.8*riser(b) + phase bonus - salience penalty
```

### Choosing the eight

Top-8-by-score clusters around one drop, so selection is a small dynamic program maximising total
score minus a gap penalty, with slot 1 forced to the first audible downbeat (MIK's cue 1 is a grid
anchor: 59 % within 0.25 s of zero) and slot 8 to an outro marker (55 % of MIK's cue 8 sits 40–90 s
from the end). Gap penalties come straight from MIK's measured gap histogram — free at 8, 16 and 32
bars, 0.15 at 12/24/48, 0.40 otherwise — with the window scaled by track length so 12-minute tracks
are not forced into 8-bar spacing. Hard constraints: the first drop must be cued, no two cues within
8 bars, at least one cue early besides slot 1.

Cues are numbered strictly by time, matching MIK's convention so A/B comparison is like-for-like.
Names carry the detector that fired (`IN`, `BLD`, `DROP`, `BRK`, `OUT`) plus the energy of the
section they open, and colours are **disjoint from MIK's** (`color_idx` 0 and 22) so you can see at
a glance whose cues you are looking at.

### Confidence and the fallback ladder

Confidence blends grid confidence, how far the chosen bars stand above the candidate median, whether
the eight actually span the track, and agreement with `PSSI` boundaries. It licenses a ladder rather
than an all-or-nothing switch:

```
high conf                     -> write our 8
medium, MIK exists            -> keep MIK, keep ours in the shadow for review
medium, no MIK                -> write ours, flagged unverified
low, MIK exists               -> keep MIK
low, no MIK                   -> cue 1 + cue 8 + PSSI chorus starts
under 90 s, or a sampler path -> nothing
```

Thresholds are not guesses — they come off the reliability plot, set so the top bucket's *measured*
agreement clears the bar.

This ladder matters more than it looks: **8,674 tracks have no MIK cue at all** (house 2,913, psy
1,846, trance 1,438, techno 908 …). That population, not the one MIK already covered, is where the
feature earns its place.

### If Stage 1 plateaus

A gradient-boosted scorer over ~60 per-downbeat features, trained on MIK's cues, using sklearn which
is already installed — 1.2 M rows, under 3 minutes to train, no new dependency. Feed its
per-bar probability into the same DP. Demucs stems and per-segment embeddings are both too slow
library-wide (4,300 h and 22 h respectively) and stay off the table unless a specific genre fails.

## Evaluation

The ground truth is smaller than it first appears: memory and hot cues in `library.db` are exact
duplicates (103,008 pairs at zero offset), so the honest count is **11,092 tracks with MIK cues,
8,263 with a complete set of eight**, not 24,000. Dedupe first or every number is inflated twofold.

- **Splits by track**, stratified on genre, BPM decade and duration: tune / eval / test, with test
  touched once, at the decision.
- **A 60-track gold set you annotate by hand**, sampled from the no-MIK population — about 7 hours
  of your time, and the only way to tell "we match MIK" from "we are good". MIK itself is only ~65 %
  precise against expert annotation, so agreement with it is a proxy with a ceiling, never the
  acceptance test.
- **Metrics**: hit rate with greedy one-to-one matching at ±0.5 s and ±1 bar; agreement at 8;
  recall at 12 (the gap between the two says whether the problem is detection or selection);
  semantic coverage (intro, first drop, outro, span); grid metrics reported separately.
- **Measured baselines as regression floors**: uniform-over-downbeats scores 0.089 at ±1 bar and
  every-32-bars scores 0.176. Anything below ~0.20 is worthless. Stage 1 targets ≥ 0.45.
- **A free whole-library metric**: agreement with `PSSI` phrase boundaries, computable over all
  19,766 tracks with no audio decode and no human. This is the nightly regression.
- **Qualitative**: a per-track strip plot (spectrogram, band energies, score curve, three rows of
  tick marks — ours, MIK's, PSSI's) rendered as contact sheets, and an audio preview that
  concatenates 6 s from each cue with a click at the cue instant.
- **The actual acceptance test is a blind A/B**: 40 tracks, two unlabelled previews, you pick.
  Stop using MIK when the win-or-tie rate clears 60 %, the first drop is cued on ≥ 90 % of tracks,
  and the confidence score is demonstrably monotone.

## Compute budget (measured on this M1 Pro, 24 real tracks, 118 min of audio)

| stage | throughput | whole library (19,766 tracks / 1,713 h) |
|---|---|---|
| Energy-led feature pass (ffmpeg decode + STFT + 64-band mel + flux), 8 workers | 4.8 tracks/s, ~1,400x realtime | **~1.2 h** |
| Same plus HPSS and CQT/chroma | ~1.8 tracks/s | **~3 h** |
| Beat This beat tracking, 3+ workers | 0.38 tracks/s, ~120x realtime | **~14.5 h** |

Beat tracking **saturates at three workers** — 3x2 threads, 6x1 and 8x1 all land within a
second of each other at 120x realtime, against 77x for a single worker using all eight
threads. It is memory-bandwidth bound, not core bound, so throwing more processes at it does
nothing.

Which means the interesting number is what we *don't* have to run: rekordbox already computed
a grid for 19,691 of these tracks, and the plan keeps them. Cue points for the whole library
therefore cost the feature pass alone — **one to three hours** — not the sixteen it would take
to re-derive every grid from audio. A new crate of 50 tracks costs about 30 seconds, or two
and a half minutes if it needs its own grid.

Cache the bar-level feature matrices (~250 KB/track, ~5 GB total). Re-tuning the detector
then replays over cache in seconds instead of hours, which is the difference between
iterating three times and three hundred.

## Environment

The analysis stack needs Python 3.12 and torch; the engine runs 3.14. Analysis therefore runs as a
worker process the engine shells out to, the way `analyze_fast.py` already did.

`beat_this` 1.1.0, torch 2.13, librosa 0.11, scipy, sklearn and soundfile all already exist in
`/Users/lemiesz/Coding/video-editor/.venv`, with the `final0` checkpoint cached — which is why the
measurements above were possible in an afternoon. **Do not depend on that venv.** Create
`crate-constellation/.venv-analysis` and pin it; `~/.essentia-venv` and `~/.rbx-audio` were both
referenced by working scripts in `library-management` and both have since vanished, which is exactly
how that pipeline broke.

## Settings

One place in the app, off by default, so nothing changes until you turn it on:

- `analysis.own_grid` — use our grid for tracks that have one, instead of rekordbox's.
- `analysis.own_cues` — write our cue points instead of the imported ones.
- `analysis.own_waveforms` — render waveforms ourselves.
- `analysis.render_anlz` — build ANLZ files from our tables instead of copying rekordbox's.
- `analysis.rekordbox_writeback` — allow the push back into `master.db`.

Stored in `library.db.meta`, read by the engine at sync time, surfaced in the sync UI so a
stick's manifest records which of them was on when it was written. With all five off,
`usb_sync` behaves exactly as it does at `working-2026-09-07`.

## Verification

- Read-only evaluators produce the gate tables; they never write.
- With every setting off, a sync must still produce a byte-identical stick to the one
  `working-2026-09-07` produces. That is the first test to write, before any of the rest.
- Every user-facing operation goes through the app's endpoints, never a scratch script.
- Deck confirmation on LORD-AI before anything touches a drive you rely on.
- One commit per finished piece, not a batch.

## Risks

- **Grid phase, not tempo, is the hard part.** Tempo is now demonstrably solved to 0.01 BPM; being a
  beat or a bar out is what makes a stick unusable, and Beat This's own downbeat labels agreed with
  rekordbox on only 64 % of tracks. Gate 1 measures phase separately for that reason.
- **`PVBR` and `PQT2` are silent failure modes** — present but degenerate and the grid vanishes with
  no error message.
- **Psytrance is the hardest genre and among the largest uncovered ones** (1,846 tracks with no MIK
  cues). Kick on every beat, structure carried by timbre, so the loudness detectors will rarely
  fire. Expect the worst scores there and let a regular 32-bar prior carry it.
- **Half/double-time** on DnB will silently turn every 8-bar gap into 16. Detected by the octave
  check against `tracks.bpm`.
- **Overfitting to MIK's mistakes** — maximising agreement at the limit reproduces MIK's ~65 %
  precision including its errors. Never accept a change that improves MIK agreement while degrading
  the gold set or `PSSI` agreement.
- **Writing to `master.db`** is the riskiest item here, which is why it is opt-in, backed up, and
  last.
