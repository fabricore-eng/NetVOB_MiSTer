#!/usr/bin/env bash
# run_memshim.sh — run the mem_shim co-sim detached, stop on frames OR stall.
#
# Usage: ./run_memshim.sh [RUNDIR] [MIN_FRAMES] [MAX_WAIT_SEC] -- [plusargs...]
#   RUNDIR      scratch dir (default ./run)
#   MIN_FRAMES  stop once this many framestore_*.ppm exist (default 2)
#   MAX_WAIT    hard wall-clock cap seconds (default 600)
# plusargs after `--` are passed to the Verilated binary, e.g.:
#   ./run_memshim.sh run 2 600 -- +ddr_rd_latency=8 +ddr_wait_period=4 +ddr_trace
#
# The sim has no natural $finish until its own watchdog/MAX-CYCLES fires, so we
# also poll and kill it (macOS has no `timeout`). We watch framestore_*.ppm
# (decode-through-mem_shim evidence) AND a "STALL/END REPORT" line in run.log.
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BIN="${HERE}/obj_dir/Vtb_memshim"

RUNDIR="${1:-${HERE}/run}"; shift || true
MIN_FRAMES="${1:-2}"; shift || true
MAX_WAIT="${1:-600}"; shift || true
# drop a leading -- separator if present
[ "${1:-}" = "--" ] && shift || true
PLUSARGS=("$@")

[ -x "$BIN" ] || { echo "ERROR: $BIN not built. make build first." >&2; exit 1; }
[ -f "${HERE}/stream.dat" ] || { echo "ERROR: stream.dat missing. make stream.dat." >&2; exit 1; }

rm -rf "$RUNDIR"; mkdir -p "$RUNDIR"
cp -f "${HERE}/stream.dat" "$RUNDIR/"

echo "plusargs: ${PLUSARGS[*]:-<none>}"
( cd "$RUNDIR" && exec "$BIN" ${PLUSARGS[@]+"${PLUSARGS[@]}"} > run.log 2>&1 ) &
PID=$!
echo "$PID" > "${RUNDIR}/sim.pid"
echo "sim PID $PID in $RUNDIR — waiting for >=${MIN_FRAMES} framestore frames OR stall (cap ${MAX_WAIT}s)..."

elapsed=0
while [ "$elapsed" -lt "$MAX_WAIT" ]; do
  c=$(ls "${RUNDIR}"/framestore_*.ppm 2>/dev/null | wc -l | tr -d ' ')
  if [ "$c" -ge "$MIN_FRAMES" ]; then echo "got $c framestore frames"; break; fi
  if grep -q "STALL/END REPORT" "${RUNDIR}/run.log" 2>/dev/null; then echo "stall report emitted"; break; fi
  if ! kill -0 "$PID" 2>/dev/null; then echo "sim exited"; break; fi
  sleep 2; elapsed=$((elapsed+2))
done

kill "$PID" 2>/dev/null || true
sleep 1
kill -9 "$PID" 2>/dev/null || true

nfs=$(ls "${RUNDIR}"/framestore_*.ppm 2>/dev/null | wc -l | tr -d ' ')
ntv=$(ls "${RUNDIR}"/tv_out_*.ppm 2>/dev/null | wc -l | tr -d ' ')
echo "frames: framestore=${nfs} tv_out=${ntv}"
echo "--- tail of run.log ---"
tail -40 "${RUNDIR}/run.log" 2>/dev/null
exit 0
