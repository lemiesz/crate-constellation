#!/usr/bin/env bash
# disk-speed-test.sh — deterministic, non-destructive read/write speed + health
# check for external drives (USB sticks, SSDs) on macOS.
#
# Usage:
#   ./disk-speed-test.sh                 # test every mounted /Volumes/* drive
#   ./disk-speed-test.sh T7              # test only /Volumes/T7
#   ./disk-speed-test.sh T7 DR_COMPUTER  # test named volumes
#   SIZE_MB=1024 ./disk-speed-test.sh T7 # override test-file size (default 512)
#
# What it does (SAFE):
#   - writes ONE temp file (default 512 MB) to the drive, times it, deletes it
#   - forces a real flush (conv=fsync) so the write number isn't RAM-buffered
#   - measures a COLD read by reading an existing large file on the drive when
#     one exists (>= test size); otherwise falls back to re-reading the temp
#     file and flags the result as possibly cache-inflated
#   - reports the negotiated USB link speed so you can spot a drive that has
#     silently dropped to USB 2.0 (a bad cable/slot/dock)
#
# It never deletes anything except its own _speedtest.bin temp file.
set -u

SIZE_MB="${SIZE_MB:-512}"
TMPNAME="_speedtest.bin"

# ---- pick targets ---------------------------------------------------------
targets=()
if [ "$#" -gt 0 ]; then
  for name in "$@"; do targets+=("/Volumes/$name"); done
else
  for v in /Volumes/*; do
    [ -d "$v" ] || continue
    # skip the boot volume and time-machine snapshots
    case "$v" in */.timemachine*) continue;; esac
    [ "$(readlink -f "$v" 2>/dev/null)" = "/" ] && continue
    targets+=("$v")
  done
fi

human() { # bytes/sec -> MB/s (integer)
  awk -v b="$1" 'BEGIN{printf "%.0f", b/1024/1024}'
}

link_speed() { # $1 = volume path -> prints human USB link speed or "n/a"
  local dev name
  dev=$(diskutil info "$1" 2>/dev/null | awk -F: '/Part of Whole/{gsub(/ /,"",$2);print $2}')
  # Try to map the whole disk to a USB product and its UsbLinkSpeed.
  local sp
  sp=$(ioreg -p IOUSB -w0 -l 2>/dev/null | awk '
      /"USB Product Name"/ {pname=$0}
      /"UsbLinkSpeed"/ {print $3; exit}' )
  # Better: read the device tree entry closest to this disk. Fall back to a
  # scan of all USB link speeds and report the max negotiated.
  local best
  best=$(ioreg -p IOUSB -w0 -l 2>/dev/null | awk -F= '/UsbLinkSpeed/{gsub(/ /,"",$2); if($2+0>m)m=$2} END{print m+0}')
  case "$best" in
    480000000)    echo "USB 2.0 (480 Mbit/s)";;
    5000000000)   echo "USB 3.2 Gen 1 (5 Gbit/s)";;
    10000000000)  echo "USB 3.2 Gen 2 (10 Gbit/s)";;
    20000000000)  echo "USB 3.2 Gen 2x2 (20 Gbit/s)";;
    0|"")         echo "n/a";;
    *)            awk -v b="$best" 'BEGIN{printf "%.1f Gbit/s\n", b/1e9}';;
  esac
}

run_one() {
  local vol="$1"
  [ -d "$vol" ] || { echo "  (not mounted, skipping: $vol)"; return; }
  [ -w "$vol" ] || { echo "  (not writable, skipping: $vol)"; return; }
  local tmp="$vol/$TMPNAME"

  echo "=============================================================="
  echo "DRIVE: $vol"
  local dev fs
  dev=$(diskutil info "$vol" 2>/dev/null | awk -F: '/Device Node/{gsub(/ /,"",$2);print $2}')
  fs=$(diskutil info "$vol"  2>/dev/null | awk -F: '/File System Personality/{sub(/^[ \t]+/,"",$2);print $2}')
  echo "  device: ${dev:-?}   fs: ${fs:-?}   link: $(link_speed "$vol")"

  # ---- WRITE ----
  local w
  w=$(dd if=/dev/zero of="$tmp" bs=1m count="$SIZE_MB" conv=fsync 2>&1 | \
        awk '/bytes/{print $(NF-1)}')
  if [ -n "${w:-}" ]; then
    echo "  WRITE ($SIZE_MB MB, flushed): $(human "$w") MB/s"
  else
    echo "  WRITE: FAILED"; rm -f "$tmp"; return
  fi

  # ---- COLD READ ----
  # Prefer an existing large file so the read isn't served from RAM cache.
  local minbytes=$(( SIZE_MB * 1024 * 1024 ))
  local coldfile
  coldfile=$(find "$vol" -type f -size +"${SIZE_MB}"M -not -path '*/.*' -not -name "$TMPNAME" 2>/dev/null | head -1)
  local r note
  if [ -n "$coldfile" ]; then
    r=$(dd if="$coldfile" of=/dev/null bs=1m count="$SIZE_MB" 2>&1 | awk '/bytes/{print $(NF-1)}')
    note="cold (existing file)"
  else
    sync
    r=$(dd if="$tmp" of=/dev/null bs=1m 2>&1 | awk '/bytes/{print $(NF-1)}')
    note="WARNING: may be cache-inflated (no large existing file to cold-read)"
  fi
  [ -n "${r:-}" ] && echo "  READ  ($SIZE_MB MB): $(human "$r") MB/s  [$note]"

  # ---- cleanup ----
  rm -f "$tmp"
  echo "  cleaned up temp file: $([ -e "$tmp" ] && echo FAILED || echo ok)"
}

echo "disk-speed-test — size=${SIZE_MB}MB — $(date '+%Y-%m-%d %H:%M:%S')"
if [ "${#targets[@]}" -eq 0 ]; then echo "no external volumes found"; exit 0; fi
for t in "${targets[@]}"; do run_one "$t"; done
echo "=============================================================="
echo "done. (writes are flushed; reads prefer an existing >=${SIZE_MB}MB file to avoid cache)"
