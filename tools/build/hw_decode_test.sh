#!/usr/bin/env bash
# tools/build/hw_decode_test.sh — turnkey morning decode-on-HW localization run.
#
# Runs the docs/hw-decode-diagnostic.md sequence in one go FROM THIS MAC:
#   1. acquire the shared `mister` device lock (dvd) via the hub
#   2. copy the candidate .rbf (480i + ADDR_ERR fix) from the Dell -> mister:/media/fat
#   3. load_core the .mgl (loads the core + mounts the test clip to S0)
#   4. verify the core switched (CORENAME=MPEG2) + MiSTer main alive
#   5. dump the fork's uart_debug telemetry (115200 8N1) — tries the HPS ttys
#   6. capture a filmstrip (HDMI path; expected black for this core — the CRT is truth)
#   7. print what to read off the CRT + how to interpret uart_debug, then RELEASE the lock
#
# YOU run this when you're at the bench watching the CRT. It does the mechanical SSH steps;
# you read the picture + (with me) the uart_debug counts to localize feed/decoder/mem_shim/video.
# Safe to re-run. Honors the shared lock so it can't collide with the 573 session.
#
# Usage: tools/build/hw_decode_test.sh
set -uo pipefail
HUB="$HOME/Dev/mister-dev-hub"
RBF_DELL="~/NetVOB_MiSTer/core/MiSTer_MPEG2/output_files/mpeg2fpga_dvd_480i_addrerr.rbf"
flt(){ grep -vE 'post-quantum|store now|openssh|vulnerable|server may'; }

echo "── 1. acquire mister devlock (dvd) ───────────────────────────"
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "mister is locked by the other session — try later"; exit 1; }
trap '"$HUB/tools/dell_coord.sh" devlock mister release dvd 2>&1 | flt' EXIT  # always release

echo "── 2. copy candidate .rbf  Dell -> mister:/media/fat ─────────"
ssh -o BatchMode=yes dell "cat $RBF_DELL" | ssh -o BatchMode=yes mister 'cat > /media/fat/mpeg2fpga_dvd.rbf; ls -la /media/fat/mpeg2fpga_dvd.rbf' 2>&1 | flt

echo "── 3. load_core the .mgl (core + clip to S0) ─────────────────"
ssh -o BatchMode=yes mister 'test -f /media/fat/mpeg2_test.mgl || printf "%s\n" "<mistergamedescription>" "  <rbf>mpeg2fpga_dvd</rbf>" "  <file delay=\"1\" type=\"s\" index=\"0\" path=\"test.mpg\"/>" "</mistergamedescription>" > /media/fat/mpeg2_test.mgl
  timeout 12 bash -c "echo load_core /media/fat/mpeg2_test.mgl > /dev/MiSTer_cmd" && echo "load_core sent"; sleep 8
  echo "CORENAME: $(cat /tmp/CORENAME 2>/dev/null)"; echo "MiSTer alive: $(ps w | grep -q "[M]iSTer /media" && echo yes || echo no)"' 2>&1 | flt

echo "── 4. uart_debug telemetry (115200 8N1; the localizer) ───────"
ssh -o BatchMode=yes mister 'for d in /dev/ttyS1 /dev/ttyS0 /dev/ttyAMA0; do
    [ -e "$d" ] || continue
    stty -F "$d" 115200 raw -echo 2>/dev/null
    line=$(timeout 5 cat "$d" 2>/dev/null | tr -d "\r" | grep -m3 .)
    if [ -n "$line" ]; then echo "[$d]"; echo "$line"; break; fi
    echo "[$d] (no data)"
  done
  echo "LED fallback: LED_DISK={streamer_active,stream_valid}, USER LED=heartbeat"' 2>&1 | flt

echo "── 5. filmstrip (HDMI/scaler path — likely BLACK; CRT is truth) ─"
"$HUB/tools/mister_filmstrip.sh" 4 2 dvd_hwtest 2>&1 | flt || true

cat <<'NEXT'
── READ THIS ─────────────────────────────────────────────────
LOOK AT THE CRT (analog/component) — that's the only true output for this core.
Interpret uart_debug (or paste it to me) per docs/hw-decode-diagnostic.md:
  streamer_* counts == 0      -> FEED bug (mpg_streamer/mount): the decoder gets no bitstream
  feed > 0, frame_cnt == 0    -> DECODER/FIFO: bitstream arrives but no frames decode
  mem_rd_count != mem_rsp_count -> MEM_SHIM response desync (the ADDR_ERR fix targets this)
  all advancing but CRT black -> VIDEO-OUT/analog (decode OK; revisit emu raster)
Candidate backups staged in core/patches/hw/: fifo-dualclock-swap, (feed-latch TODO).
The device lock auto-releases when this script exits.
NEXT
