#!/usr/bin/env bash
# run_sim.sh — run the Verilated decoder bench and stop it once frames exist.
#
# The upstream testbench has no end-of-stream $finish: after the MPEG-2 clip
# drains it free-runs forever. So we launch it detached, poll for output PPMs,
# then stop it. (macOS has no `timeout`/`gtimeout`, hence the poll loop.)
#
# Usage: ./run_sim.sh [RUNDIR] [MIN_FRAMES] [MAX_WAIT_SEC]
#   RUNDIR      : scratch dir for the run (created/cleared). Default: ./run
#   MIN_FRAMES  : stop once this many tv_out_*.ppm exist. Default: 2
#                 (frame 0 + a complete frame 1 is enough to render a PNG)
#   MAX_WAIT_SEC: hard wall-clock cap. Default: 120
#
# Note: deliberately NOT using `set -e` — process control (kill of an
# already-exited PID) and `ls` globs that match nothing legitimately return
# non-zero and must not abort the run. The script always exits 0 on a clean stop.
set -uo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HERE}/obj_dir/Vtestbench"
RUNDIR="${1:-${HERE}/run}"
MIN_FRAMES="${2:-2}"
MAX_WAIT="${3:-120}"

[ -x "$BIN" ] || { echo "ERROR: $BIN not built. Run 'make build' first." >&2; exit 1; }
[ -f "${HERE}/stream.dat" ] || { echo "ERROR: stream.dat missing. Run 'make stream.dat'." >&2; exit 1; }

rm -rf "$RUNDIR"; mkdir -p "$RUNDIR"
cp -f "${HERE}/stream.dat" "$RUNDIR/"

( cd "$RUNDIR" && exec "$BIN" > run.log 2>&1 ) &
PID=$!
echo "$PID" > "${RUNDIR}/sim.pid"
echo "sim PID $PID in $RUNDIR — waiting for >= $MIN_FRAMES frames (cap ${MAX_WAIT}s)..."

elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT" ]; do
  c=$(ls "${RUNDIR}"/tv_out_*.ppm 2>/dev/null | wc -l | tr -d ' ')
  if [ "$c" -ge "$MIN_FRAMES" ]; then break; fi
  if ! kill -0 "$PID" 2>/dev/null; then echo "sim exited early"; break; fi
  sleep 1; elapsed=$((elapsed+1))
done

kill "$PID" 2>/dev/null || true
sleep 1
kill -9 "$PID" 2>/dev/null || true

nframes=$(ls "${RUNDIR}"/tv_out_*.ppm 2>/dev/null | wc -l | tr -d ' ')
nstore=$(ls "${RUNDIR}"/framestore_*.ppm 2>/dev/null | wc -l | tr -d ' ')
echo "frames: tv_out=${nframes} framestore=${nstore}"
# Succeed iff we actually produced at least MIN_FRAMES rasters.
[ "${nframes:-0}" -ge "$MIN_FRAMES" ] && exit 0 || exit 1
