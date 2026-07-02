#!/usr/bin/env bash
# run_stall_grid.sh — the slice-2-stall oracle experiment grid (2026-07-01).
#
# HW ground truth to reproduce (tools/hw_gate_runs/sdcfix{,2}_20260701/):
#   slice 1 of frame 0 decodes near-bit-perfect -> corruption onset at a BOOT-VARYING
#   point early in slice 2 -> decode writes STOP -> stream still consumed to EOF ->
#   reads continue ~32k/s forever in FRAME windows. No shim recovery events.
#
# Arms (all vs the same dense clip stream.dat = the HW clip; base lat30):
#   control   : no fault knobs; run past stream end + soak -> the HEALTHY terminal
#               signature (never observed before!). Compare its end-state vs HW's.
#   rawN      : +ddr_wr_commit_delay=N — the f2sdram read-after-posted-write hazard.
#               RAW-STALE prints tell us when/where staleness bites (vbuf vs frame
#               windows). Expect: desync early in a slice if this is the mechanism.
#   crd/cwr   : seeded single-bit corruption of read responses / committed writes in
#               the vbuf window only (the placement/setup-marginality proxy).
#   refresh   : periodic full-bus outages (+ddr_refresh_period/hold).
#
# Verdict criteria per arm (grep the run dir):
#   REPRO = framestore_0000.ppm PARTIAL (top band only) + "STALL/END REPORT" with
#           ingest continuing past decode death (mimics J==Z + write silence).
#   Also inspect: RAW-STALE / CORRUPT-* prints, watchdog re-clear events (W jump).
#
# Usage: ./run_stall_grid.sh [ARM]   (no arg = list arms)
set -uo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VBUF_LO=1c0000   # word addrs, MP_AT_HL map (mem_codes.v): the bitstream ring
VBUF_HI=1efffe

arm="${1:-list}"
case "$arm" in
  control)
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_control" 99 7200 -- \
      +ddr_rd_latency=30 +max_cycles=400000000 ;;
  raw8)
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_raw8" 4 1800 -- \
      +ddr_rd_latency=30 +ddr_wr_commit_delay=8 ;;
  raw32)
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_raw32" 4 1800 -- \
      +ddr_rd_latency=30 +ddr_wr_commit_delay=32 ;;
  raw128)
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_raw128" 4 1800 -- \
      +ddr_rd_latency=30 +ddr_wr_commit_delay=128 ;;
  crd)  # corrupt ~1/50k vbuf-window read responses, seeded
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_crd" 4 1800 -- \
      +ddr_rd_latency=30 +ddr_corrupt_rd=50000 +ddr_corrupt_lo=$VBUF_LO +ddr_corrupt_hi=$VBUF_HI +ddr_seed=1 ;;
  cwr)  # corrupt ~1/50k vbuf-window committed writes, seeded
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_cwr" 4 1800 -- \
      +ddr_rd_latency=30 +ddr_corrupt_wr=50000 +ddr_corrupt_lo=$VBUF_LO +ddr_corrupt_hi=$VBUF_HI +ddr_seed=1 ;;
  refresh)  # ~7.8us-period 350-cycle outages (DDR3 tREFI/tRFC-ish at 108MHz)
    exec "$HERE/run_memshim.sh" "$HERE/run_grid_refresh" 4 1800 -- \
      +ddr_rd_latency=30 +ddr_refresh_period=842 +ddr_refresh_hold=38 ;;
  list|*)
    grep -E '^  [a-z0-9]+\)' "$0" | tr -d ')' ; exit 0 ;;
esac
