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
RBF_DELL="~/NetVOB_MiSTer/core/MiSTer_MPEG2/output_files/mpeg2fpga_dvd_readrecovery.rbf"
# The golden NTSC 480i ES clip — proven to decode in sim (core/sim). Staging THIS to the
# board removes the file-not-found ambiguity: if the feed is still empty with a confirmed
# non-zero file on disk, the bug is the mount PULSE (RTL), not a missing/zero-size file.
CLIP_LOCAL="$(cd "$(dirname "$0")/../.." && pwd)/tools/testclips/test480i_ntsc.m2v"
flt(){ grep -vE 'post-quantum|store now|openssh|vulnerable|server may'; }
[ -f "$CLIP_LOCAL" ] || { echo "regenerating golden clip..."; "$(dirname "$0")/../testclips/make_test480i.sh"; }

echo "── 1. acquire mister devlock (dvd) ───────────────────────────"
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "mister is locked by the other session — try later"; exit 1; }
# On exit: return the board to the MENU before releasing. The mpeg2 test core hangs on the
# DDR bug and its watchdog resets the decoder ~every 4s, which re-triggers the MiSTer
# resolution OSD over and over on the TV. Loading the menu stops that so we don't leave a
# flickering board for the other session / the human. (verified annoyance 2026-06-04)
cleanup(){ ssh -o BatchMode=yes mister 'echo "load_core /media/fat/menu.rbf" > /dev/MiSTer_cmd' 2>&1 | flt; "$HUB/tools/dell_coord.sh" devlock mister release dvd 2>&1 | flt; }
trap cleanup EXIT

echo "── 2. copy candidate .rbf  Dell -> mister:/media/fat ─────────"
ssh -o BatchMode=yes dell "cat $RBF_DELL" | ssh -o BatchMode=yes mister 'cat > /media/fat/mpeg2fpga_dvd.rbf; ls -la /media/fat/mpeg2fpga_dvd.rbf' 2>&1 | flt

echo "── 2b. stage golden clip  Mac -> mister:/media/fat/test.mpg ──"
# THIS is the step the original helper was missing: the .mgl mounts path="test.mpg"
# but nothing put a test.mpg on the board -> img_size=0 -> streamer total_sectors=0 -> black.
ssh -o BatchMode=yes mister "cat > /media/fat/test.mpg" < "$CLIP_LOCAL" 2>&1 | flt
ssh -o BatchMode=yes mister 'sz=$(wc -c < /media/fat/test.mpg 2>/dev/null || echo 0)
  echo "test.mpg on board: ${sz} bytes  $( [ "$sz" -gt 0 ] && echo "(file present -> img_size should be non-zero)" || echo "(EMPTY/MISSING -> would read as file-not-found)" )"
  echo "first4: $(xxd -p -l 4 /media/fat/test.mpg 2>/dev/null) (expect 000001b3 = MPEG-2 seq header)"' 2>&1 | flt

echo "── 3. load_core the .mgl (core + clip to S0) ─────────────────"
# NOTE (verified on HW 2026-06-04): the .mgl <file path> MUST be ABSOLUTE. A relative
# path="test.mpg" pulses img_mounted but the framework reports img_size=0 (file not found
# at the resolved base) -> total_sectors=0 -> empty feed. "/media/fat/test.mpg" resolves
# correctly -> img_size + total_sectors correct -> streamer feeds the decoder. Always
# (re)write the .mgl so a stale relative one can't linger.
ssh -o BatchMode=yes mister 'printf "%s\n" "<mistergamedescription>" "  <rbf>mpeg2fpga_dvd</rbf>" "  <file delay=\"2\" type=\"s\" index=\"0\" path=\"/media/fat/test.mpg\"/>" "</mistergamedescription>" > /media/fat/mpeg2_test.mgl
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
A non-zero test.mpg is now staged (step 2b), so the FEED branch is now DECISIVE:
the decode gate can be read from uart_debug ALONE — you do NOT need the CRT for it
(the HDMI screenshot is black by design for this raw-VGA core; the CRT is only the
final analog field/color check AFTER decode is confirmed).

Interpret uart_debug (Z=streamer_total_sectors, J=next_lba, T=active; paste it to me):
  Z == 0  AND step-2b said EMPTY/MISSING  -> file-not-found: fix the path/copy, re-run.
  Z == 0  BUT step-2b confirmed a file    -> MOUNT-PULSE bug: img_mounted[0] never fired
                                             (the .mgl mount didn't reach the S0 slot).
                                             SPLIT IT (hub LESSONS): open the OSD and use
                                             "Load Video" to mount test.mpg BY HAND.
                                               OSD mount works -> .mgl syntax/timing; fix .mgl.
                                               OSD mount also Z=0 -> core img_mounted/sd_* RTL.
  Z >  0, frame_cnt(FC) == 0   -> DECODER/FIFO: bitstream arrives but no frames decode.
  mem_rd(P) != mem_rsp(RP)     -> MEM_SHIM response desync (the ADDR_ERR fix targets this).
  Z>0, FC advancing, CRT black -> VIDEO-OUT/analog only (decode OK; revisit emu raster).
Candidate backups staged in core/patches/hw/: fifo-dualclock-swap, (sticky-mount-latch TODO).
The device lock auto-releases when this script exits.
NEXT
