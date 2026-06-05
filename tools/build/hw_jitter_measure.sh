#!/usr/bin/env bash
# hw_jitter_measure.sh — probe-free decode-desync / jitter verdict for the MPEG-2 core.
#
# Re-decodes the staged clip N times on the mister and measures, for each run, how far
# down the frame the decoder got before it stops writing (the desync cutoff), by reading
# the FRAME_0 luma plane straight out of DDR via /dev/mem (NO RTL probe -> no f2sdram
# wedge). Reports the per-run stall MB-row and whether it JITTERS.
#
# Why this exists: the mid-frame desync was a posted-write commit-visibility ORDERING RACE
# on the vbuf ring (write path byte-perfect, counts match, FIFO+CDC exonerated) -> the
# desync row JITTERS run-to-run [observed 1,6,7,10] on an identical bitstream. The fix is a
# vbuf read-behind safety gap (framestore_request VBUF_READ_GAP). VERDICT:
#   - all runs cutoff = row 29 (full frame), no jitter  => FIXED (race fenced)
#   - cutoff still jitters / < 29                        => not fixed (raise VBUF_READ_GAP or wrong mechanism)
#
# Assumes: .mgl mpeg2_test staged (rbf mpeg2fpga_dvd + /media/fat/test.mpg); `ssh mister` works.
# Does NOT flash a build or reboot — run those first if you just built. Re-stages the dump tool
# (tmpfs is wiped by reboot). One writer: it only reads DDR; safe to re-run.
#
# Usage: tools/build/hw_jitter_measure.sh [N_RUNS] [SETTLE_SEC]
#   N_RUNS     : number of reload+measure runs (default 5)
#   SETTLE_SEC : seconds to let decode settle before dumping (default 20)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
N="${1:-5}"
SETTLE="${2:-20}"
MISTER="${MISTER:-mister}"
DUMP="${HERE}/dump_framestore.py"
flt(){ grep -vE 'WARNING|post-quantum|store now|may need|openssh' ; }

[ -f "$DUMP" ] || { echo "ERROR: $DUMP not found" >&2; exit 1; }
echo "── re-stage dump tool (tmpfs wiped by reboot) ──"
scp -q "$DUMP" "${MISTER}:/tmp/dump_framestore.py" 2>&1 | flt

TMP="$(mktemp -d)"
echo "── ${N} reload+decode+dump runs (settle ${SETTLE}s), FRAME_0 Y via /dev/mem ──"
for n in $(seq 1 "$N"); do
  ssh -o BatchMode=yes "$MISTER" "echo load_core /media/fat/mpeg2_test.mgl > /dev/MiSTer_cmd; \
    sleep ${SETTLE}; python3 /tmp/dump_framestore.py 0x30000000 0x54600 > /tmp/f0.bin 2>/dev/null; echo run_${n}_done" 2>/dev/null | flt
  scp -q "${MISTER}:/tmp/f0.bin" "${TMP}/f0_${n}.bin" 2>/dev/null
done

python3 - "$TMP" "$N" <<'PY'
import sys
W,H=720,480
tmp,N=sys.argv[1],int(sys.argv[2])
def rowstd(d, mb):
    # stddev over a full MB-row band (16 lines x 720 px). A cleanly-decoded test-pattern row
    # crosses all 7 color bars + the gradient -> HIGH stddev (~50-70). Undecoded = framestore
    # init 128 -> ~0. Desync NOISE = mostly-128 + sparse speckle -> LOW stddev (~10-18). So a
    # stddev threshold robustly separates real decode from speckle (the old mean!=128 metric did
    # NOT — speckle shifted the mean and faked a full frame).
    band=d[mb*16*W:(mb*16+16)*W]; n=len(band)
    if n==0: return 0.0
    mean=sum(band)/n
    return (sum((v-mean)*(v-mean) for v in band)/n) ** 0.5
def stall(fn):
    try: d=open(fn,'rb').read(W*H)
    except: return None
    if len(d)<W*H: return None
    # last MB-row of the CONTIGUOUS cleanly-decoded run from the top (decode degrades to noise
    # from the desync down, so the clean region is a top-contiguous prefix; ignore speckle below).
    last=-1
    for mb in range(30):
        if rowstd(d, mb) > 30.0: last=mb
        else: break
    return last
res=[stall(f"{tmp}/f0_{n}.bin") for n in range(1,N+1)]
print(f"\nstall MB-row per run: {res}")
good=[r for r in res if r is not None]
if not good:
    print("VERDICT: NO DECODE (blank framestore) — wedge or dead build."); sys.exit(0)
uniq=sorted(set(good))
full=all(r==29 for r in good)
jitter=len(uniq)>1
if full:
    print("VERDICT: ✅ FIXED — every run decoded the FULL frame (row 29), no jitter. Ordering race fenced.")
elif jitter:
    print(f"VERDICT: ❌ STILL JITTERS {uniq} — race not fully fenced (raise VBUF_READ_GAP, or wrong mechanism).")
else:
    print(f"VERDICT: ⚠️ STABLE at row {uniq[0]} (no jitter) — deterministic stop, NOT the jitter race; investigate that row.")
PY
rm -rf "$TMP"
