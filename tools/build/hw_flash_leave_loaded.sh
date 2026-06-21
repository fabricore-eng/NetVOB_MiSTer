#!/usr/bin/env bash
# hw_flash_leave_loaded.sh — flash a PRE-BUILT dell .rbf de-confounded and LEAVE IT LOADED
# with the devlock HELD, for an external inspector (cockpit dashboard) to verify the video
# signal. De-confounded = warm-reboot (clean HPS f2sdram bridge) + EXACTLY ONE load_core.
# Does NOT cleanup-unload and does NOT release the lock — caller releases after confirmation:
#   ~/Dev/tools/tools/dell_coord.sh devlock mister release dvd
#
# Usage: tools/build/hw_flash_leave_loaded.sh [RBF_BASENAME]   (default: mpeg2fpga_dvd_revertvid.rbf)
set -uo pipefail
RBF="${1:-mpeg2fpga_dvd_revertvid.rbf}"
HUB="$HOME/Dev/tools"
REPO_DELL="~/NetVOB_MiSTer/core/MiSTer_MPEG2"
RBF_DELL="$REPO_DELL/output_files/${RBF}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLIP_LOCAL="$ROOT/tools/testclips/test480i_ntsc.m2v"
flt(){ grep -vE 'post-quantum|store now|openssh|vulnerable|server may|upgraded'; }
say(){ printf '\n── %s\n' "$*"; }

say "1. acquire devlock (dvd)  [GATE]"
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "LOCKED — abort"; exit 1; }

say "2. warm-reboot (de-confound: clean HPS f2sdram bridge)"
"$HUB/tools/dell_coord.sh" devlock mister reboot dvd 2>&1 | flt || { echo "reboot refused — abort"; exit 1; }
up=0; for i in $(seq 1 36); do ssh -o BatchMode=yes -o ConnectTimeout=4 mister 'test -e /dev/MiSTer_cmd' 2>/dev/null && { up=1; echo "up after ~$((i*5))s"; break; }; sleep 5; done
[ "$up" = 1 ] || { echo "no comeback — abort"; exit 2; }
sleep 5
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "re-acquire failed — abort"; exit 1; }

say "3. stage rbf + clip + mgl"
ssh -o BatchMode=yes dell "cat $RBF_DELL" | ssh -o BatchMode=yes mister "cat > /media/fat/mpeg2fpga_dvd.rbf; md5sum /media/fat/mpeg2fpga_dvd.rbf" 2>&1 | flt
ssh -o BatchMode=yes dell "md5sum $RBF_DELL" 2>&1 | flt
ssh -o BatchMode=yes mister "cat > /media/fat/test.mpg" < "$CLIP_LOCAL" 2>&1 | flt
ssh -o BatchMode=yes mister 'printf "%s\n" "<mistergamedescription>" "  <rbf>mpeg2fpga_dvd</rbf>" "  <file delay=\"2\" type=\"s\" index=\"0\" path=\"/media/fat/test.mpg\"/>" "</mistergamedescription>" > /media/fat/mpeg2_test.mgl' 2>&1 | flt

say "4. ONE load_core (de-confounded bridge)"
ssh -o BatchMode=yes mister 'timeout 12 bash -c "echo load_core /media/fat/mpeg2_test.mgl > /dev/MiSTer_cmd" && echo "load_core sent"; sleep 9
  echo "CORENAME: $(cat /tmp/CORENAME 2>/dev/null)  MiSTer-alive: $(ps w | grep -q "[M]iSTer /media" && echo yes || echo no)"' 2>&1 | flt

say "5. uart telemetry (confirm video/core alive)"
ssh -o BatchMode=yes mister 'for d in /dev/ttyS1 /dev/ttyS0; do [ -e "$d" ] || continue
    stty -F "$d" 115200 raw -echo 2>/dev/null
    line=$(timeout 5 cat "$d" 2>/dev/null | tr -d "\r" | grep -m2 .)
    [ -n "$line" ] && { echo "[$d]"; echo "$line"; break; }; done' 2>&1 | flt

cat <<'NEXT'

── LEFT LOADED + LOCK HELD ───────────────────────────────────
The mpeg2fpga_dvd core (HALFLINE=0) is loaded on a DE-CONFOUNDED bridge (warm-reboot + ONE load_core).
The devlock is HELD (dvd) so it stays up + undisturbed. cockpit: inspect the dashboard live-screen +
read the video-mode. To release when done:
  ~/Dev/tools/tools/dell_coord.sh devlock mister release dvd
NEXT
