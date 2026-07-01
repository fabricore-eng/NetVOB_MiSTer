#!/usr/bin/env bash
# =============================================================================
# capture_dvd.sh -- ONE command from the Mac: ARM the dvd f2sdram WRITE-WEDGE
# SignalTap trigger on the DE10 (via dell's USB-Blaster II, dockerized Quartus
# 17.0), wait, export CSV, copy results back to tools/signaltap/captures/.
#
#   tools/signaltap/capture_dvd.sh [timeout_seconds]        (default 120)
#
# PREREQS (see docs/HANDOFF-2026-07-01-signaltap-writewedge.md + the 573
# RUNBOOK): the INSTRUMENTED write_wedge .rbf/.sof is already RUNNING on the
# DE10 (warm-reboot + ONE load_core of a .mgl whose clip mount is DELAYED ~20s),
# and you START THIS SCRIPT INSIDE that idle window so the analyzer is armed
# BEFORE the delayed clip mount runs the framestore CLEAR that wedges the bridge
# (trigger = mem_shim `wedged` high, post-position). This script touches ONLY
# JTAG -- it never builds, never programs the FPGA, never touches the SD.
#
# The trigger firing IS the heisenbug gate: FIRES  => the instrumented build
# still wedges (a valid capture of the onset); TIMES OUT => no wedge this boot
# (a good write placement re-rolled) -> STOP, do not interpret; retest the
# age-gate for a frame instead.
#
# Reuses the 573 session's GENERIC capture_headless.tcl on dell (parameterized
# by -signal_set/-trigger/-dev) and its read_stp_csv.py decoder for the CSV.
# =============================================================================
set -uo pipefail

TIMEOUT="${1:-120}"
TS="$(date +%Y%m%d_%H%M%S)"
HERE="$(cd "$(dirname "$0")" && pwd)"
OUT_DIR="$HERE/captures/write_wedge_$TS"
IMG="raetro/quartus:17.0"
STP_DELL='$HOME/NetVOB_MiSTer/core/MiSTer_MPEG2/write_wedge.stp'  # expanded ON dell
TCL_573='$HOME/System573_MiSTer/tools/signaltap_573/capture_headless.tcl'
WORK="/tmp/stp_dvd_$TS"            # scratch ON dell (close_session writes back into the .stp)
SIGNAL_SET="ss_write_wedge"
TRIGGER="trig_write_wedge"
DEV_GLOB='*5CSEBA6*'              # DE10-Nano == mister part (5CSEBA6U23I7)

mkdir -p "$OUT_DIR"
flt(){ grep -vE 'post-quantum|store now|openssh|vulnerable|server may|upgraded'; }

echo "== [1/4] preflight: JTAG chain on dell (DE10 USB-Blaster II) =="
if ! ssh -o BatchMode=yes dell "docker run --rm --name jtag-dvd --privileged -v /dev/bus/usb:/dev/bus/usb $IMG jtagconfig" 2>&1 | flt | grep -q "DE-SoC"; then
    echo "FATAL: no DE-SoC JTAG chain on dell (USB-Blaster II unplugged, de10 off, or container/USB issue)."
    exit 1
fi

echo "== [2/4] staging scratch .stp on dell ($WORK) =="
ssh -o BatchMode=yes dell "mkdir -p $WORK && cp $STP_DELL $WORK/run.stp && ls -l $WORK/run.stp" 2>&1 | flt

echo "== [3/4] arming trigger '$TRIGGER' (timeout ${TIMEOUT}s) -- the delayed clip mount must fire the wedge within this window =="
set +e
ssh -o BatchMode=yes dell "docker run --rm --name jtag-dvd --privileged \
    -v /dev/bus/usb:/dev/bus/usb \
    -v \$HOME/System573_MiSTer:/build:ro \
    -v $WORK:/work \
    $IMG quartus_stp -t /build/tools/signaltap_573/capture_headless.tcl \
        -stp /work/run.stp -csv /work/write_wedge_$TS.csv -timeout $TIMEOUT \
        -signal_set $SIGNAL_SET -trigger $TRIGGER -dev '$DEV_GLOB'" \
  2>&1 | flt | tee "$OUT_DIR/quartus_stp_$TS.log"
RC=${PIPESTATUS[0]}
set -e

# The tcl 'run' PRINTS arm failures without throwing (573 cost a diagnosis cycle
# 2026-06-10: bogus TRIGGERED after Error 261009). Grade from the full output.
if grep -q 'Error (261009)' "$OUT_DIR/quartus_stp_$TS.log"; then
    echo "VERDICT: ARM FAILED (261009 stp/device CRC mismatch) -- analyzer never armed."
    echo " - the running .rbf's crc[] ties must match the .stp CRC attr (RUNBOOK 'CRC gate')."
    exit 3
fi
if grep -q 'Internal Error' "$OUT_DIR/quartus_stp_$TS.log"; then
    echo "VERDICT: quartus_stp CRASHED (Internal Error) -- see $OUT_DIR/quartus_stp_$TS.log"; exit 4
fi

echo "== [4/4] retrieving results =="
scp -q -o BatchMode=yes "dell:$WORK/write_wedge_$TS.csv" "$OUT_DIR/" 2>/dev/null || true
scp -q -o BatchMode=yes "dell:$WORK/run.stp"             "$OUT_DIR/write_wedge_${TS}_with_log.stp" 2>/dev/null || true
ssh -o BatchMode=yes dell "rm -rf $WORK" 2>&1 | flt || true

case $RC in
  0)  echo "CAPTURE OK -> $OUT_DIR/write_wedge_$TS.csv"
      echo "DECODE (573 decoder handles the trigger-channel-drop CSV quirk):"
      echo "  python3 ~/Dev/fabricore/System573_MiSTer/tools/signaltap_573/read_stp_csv.py audit $OUT_DIR/write_wedge_$TS.csv"
      echo "  # then: ... print --signals st_waitreq,wedged,ram_write,ram_read,state,wr_count,outstanding_reads --range <onset>"
      echo "VERDICT DECISION TREE: docs/HANDOFF-2026-07-01-signaltap-writewedge.md (st_waitreq toggle-then-stick=bridge-side; sticks-on-a-write=boundary register)." ;;
  2)  echo "NO TRIGGER within ${TIMEOUT}s = NO WEDGE this boot (heisenbug: a good write placement re-rolled)."
      echo " - Confirm via UART (U should NOT be stuck 1, W not frozen). If truly no wedge: STOP interpreting;"
      echo "   accept the lucky placement + retest the age-gate for a FRAME instead, OR re-roll with a nudge."
      exit 2 ;;
  *)  echo "CAPTURE FAILED (rc=$RC). 'Trigger not compatible with device' => the running .rbf is NOT this instrumented build (or the .stp node list/depth changed since the build)."
      exit 1 ;;
esac
