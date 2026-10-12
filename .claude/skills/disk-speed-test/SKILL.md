---
name: disk-speed-test
description: Benchmark read/write speed and USB link speed of external drives (USB sticks, SSDs like the T7, DJ export drives) on macOS. Use when the user asks to test a drive's speed, health, or whether a cable/slot/dock is bottlenecking a drive to USB 2.0. Non-destructive.
---

# Disk speed test

Deterministic, non-destructive read/write speed + link-speed check for external
drives on macOS. Built to diagnose the "why is my export/drive slow?" question —
especially catching a fast drive silently negotiating USB 2.0 because of a bad
cable, slot, or dock.

## Running it

The script is bundled next to this file. Run it directly:

**Always name the volume.** With no arguments the script writes to *every* mounted
external drive, and `T7` / `drcomputer-backup(s)` are off-limits unless the user asked
for that drive in this conversation.

```bash
.claude/skills/disk-speed-test/disk-speed-test.sh LORD-AI               # one named volume
.claude/skills/disk-speed-test/disk-speed-test.sh LORD-AI DR_COMPUTER   # several
SIZE_MB=1024 .claude/skills/disk-speed-test/disk-speed-test.sh LORD-AI  # bigger sample = steadier numbers
```


## What it does — and why it's safe

- Writes ONE temp file (`_speedtest.bin`, default 512 MB) to the drive, times it,
  then deletes it. It never touches any other file.
- Uses `dd ... conv=fsync` so the write number is a real flushed write, not RAM
  buffering.
- Measures a COLD read by reading an existing large file (>= test size) already
  on the drive, so the read isn't served from the OS page cache. Falls back to
  re-reading the temp file and flags the number as possibly cache-inflated if no
  large file exists.
- Reports the negotiated USB link speed (e.g. "USB 2.0 (480 Mbit/s)" vs
  "USB 3.2 Gen 2 (10 Gbit/s)") so a drive that dropped to USB 2.0 is obvious.

## Interpreting results

- **Link speed is the first thing to read.** A USB 3 drive showing
  `USB 2.0 (480 Mbit/s)` and ~40 MB/s means a bad cable/slot/dock path — swap the
  cable/slot or plug direct to the Mac, not a dead drive.
- **Read fast but write slow** on an SSD usually means the drive is nearly full
  (shrunken SLC cache) or wear — not a connection problem. Read regressions are
  the earlier warning sign of a failing drive.
- **Real SMART/wear data can't be read over USB on macOS** (no SAT pass-through;
  smartctl returns "Not a device of type scsi"). For wear %, TBW, and power-on
  hours, plug the drive into Linux/Windows (`smartctl`, CrystalDiskInfo) or use
  the vendor app. This script's read-speed + `diskutil verifyVolume` + system-log
  I/O-error check are the available proxies on macOS.

## Context

Written for a DJ workflow: rekordbox library on a Samsung T7, tracks exported to
USB sticks (e.g. DR_COMPUTER) for CDJs. Note CDJs read USB at ~USB 2.0 speeds, so
export-drive throughput above that is headroom, not a gig-day bottleneck.
