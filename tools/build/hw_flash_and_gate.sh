#!/usr/bin/env bash
# tools/build/hw_flash_and_gate.sh — flash the current dell .sof to the mister and run the
# OBJECTIVE decode gate (DDR framestore -> hw_decode_verify.sh). One turnkey milestone run.
#
# Flow (all gated on the shared `mister` devlock so it can't collide with the 573 session):
#   1. convert dell:output_files/mpeg2fpga.sof -> a tagged .rbf (quartus_cpf in docker)
#   2. acquire mister devlock (dvd)  [GATE — disruptive HW actions require this]
#   3. copy .rbf Dell->mister:/media/fat + stage the golden 480i clip
#   4. write an ABSOLUTE-path .mgl + load_core (core + clip to S0)
#   5. read uart_debug telemetry (115200 8N1): Z=total_sectors J=next_lba FC=frame_cnt P/RP=mem rd/rsp
#   6. probe-free /dev/mem framestore dump on the mister -> pull to Mac -> hw_decode_verify.sh gate
#   7. (bonus) HDMI screenshot of the OSD for the scanout/interlace triangulation
#   8. cleanup: load menu.rbf (stops the res-OSD flicker) + RELEASE the lock
#
# Usage: tools/build/hw_flash_and_gate.sh [TAG]   (TAG default: bracket)
# Prints raw numbers + the gate VERDICT. Does NOT claim a milestone — the human/orchestrator does.
set -uo pipefail
TAG="${1:-bracket}"
HUB="${FABRICORE_HUB:-$HOME/Dev/fabricore/tools}"; [ -d "$HUB" ] || HUB="$HOME/Dev/tools"
REPO_DELL="~/NetVOB_MiSTer/core/MiSTer_MPEG2"
SOF_DELL="$REPO_DELL/output_files/mpeg2fpga.sof"
RBF_NAME="mpeg2fpga_dvd_${TAG}.rbf"
RBF_DELL="$REPO_DELL/output_files/${RBF_NAME}"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CLIP_LOCAL="$ROOT/tools/testclips/test480i_ntsc.m2v"
DUMP_PY="$ROOT/tools/build/dump_framestore.py"
GATE="$ROOT/tools/build/hw_decode_verify.sh"
OUT="/tmp/hw_gate_${TAG}"
mkdir -p "$OUT"
flt(){ grep -vE 'post-quantum|store now|openssh|vulnerable|server may|upgraded'; }
say(){ printf '\n── %s\n' "$*"; }

[ -f "$CLIP_LOCAL" ] || { echo "regen golden clip..."; "$ROOT/tools/testclips/make_test480i.sh" >/dev/null 2>&1; }

say "1. sof -> rbf  (quartus_cpf docker, compressed)"
ssh -o BatchMode=yes dell "cd $REPO_DELL && timeout 240 docker run --rm -v \"\$PWD\":/work -w /work raetro/quartus:17.0 \
  quartus_cpf -c -o bitstream_compression=on output_files/mpeg2fpga.sof output_files/${RBF_NAME} >/dev/null 2>&1; \
  ls -la output_files/${RBF_NAME} 2>/dev/null" 2>&1 | flt
RBFSZ=$(ssh -o BatchMode=yes dell "wc -c < $RBF_DELL 2>/dev/null" 2>/dev/null | tr -d '[:space:]')
echo "rbf bytes: ${RBFSZ:-0}  (sane MiSTer rbf ~2.9-3.1M)"
[ "${RBFSZ:-0}" -gt 2000000 ] 2>/dev/null || { echo "!! rbf conversion looks wrong (size ${RBFSZ}) — ABORT before touching HW"; exit 3; }

say "2. acquire mister devlock (dvd)  [GATE]"
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "mister LOCKED by other session — abort, retry later"; exit 1; }
cleanup(){ ssh -o BatchMode=yes mister 'echo "load_core /media/fat/menu.rbf" > /dev/MiSTer_cmd' 2>&1 | flt; "$HUB/tools/dell_coord.sh" devlock mister release dvd 2>&1 | flt; }
trap cleanup EXIT

say "2b. WARM REBOOT to clear any STALE HPS f2sdram wedge  [CRITICAL for this core]"
# Lesson (memory: feed-gate-solved): the f2sdram bridge wedges and a WARM REBOOT clears it
# while a core-reload (load_core, FPGA reconfig) does NOT — load_core leaves the HPS-side
# f2sdram controller in whatever state the prior core/session left it. So we MUST reboot
# (resets the HPS bridge) and load our core onto a CLEAN bridge, else we inherit a stale
# wedge (e.g. from the 573 session's probe builds) and misread it as OUR build wedging.
"$HUB/tools/dell_coord.sh" devlock mister reboot dvd 2>&1 | flt || { echo "reboot refused (someone else holds lock) — abort"; exit 1; }
echo "rebooting; waiting for mister to come back up..."
up=0
for i in $(seq 1 36); do
  if ssh -o BatchMode=yes -o ConnectTimeout=4 mister 'test -e /dev/MiSTer_cmd' 2>/dev/null; then up=1; echo "mister up after ~$((i*5))s"; break; fi
  sleep 5
done
[ "$up" = 1 ] || { echo "mister did not come back within 180s — abort"; exit 2; }
sleep 5
# reboot WIPES the on-board lock -> re-acquire so 573 can't grab it mid-test
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "could not re-acquire lock after reboot — abort"; exit 1; }

say "3. copy rbf Dell->mister + stage clip"
ssh -o BatchMode=yes dell "cat $RBF_DELL" | ssh -o BatchMode=yes mister "cat > /media/fat/mpeg2fpga_dvd.rbf; md5sum /media/fat/mpeg2fpga_dvd.rbf" 2>&1 | flt
ssh -o BatchMode=yes dell "md5sum $RBF_DELL" 2>&1 | flt
ssh -o BatchMode=yes mister "cat > /media/fat/test.mpg" < "$CLIP_LOCAL" 2>&1 | flt
ssh -o BatchMode=yes mister 'echo "test.mpg: $(wc -c < /media/fat/test.mpg) bytes, first4 $(xxd -p -l4 /media/fat/test.mpg)"' 2>&1 | flt

say "4. write absolute-path .mgl + load_core"
ssh -o BatchMode=yes mister 'printf "%s\n" "<mistergamedescription>" "  <rbf>mpeg2fpga_dvd</rbf>" "  <file delay=\"2\" type=\"s\" index=\"0\" path=\"/media/fat/test.mpg\"/>" "</mistergamedescription>" > /media/fat/mpeg2_test.mgl
  timeout 12 bash -c "echo load_core /media/fat/mpeg2_test.mgl > /dev/MiSTer_cmd" && echo load_core sent; sleep 9
  echo "CORENAME: $(cat /tmp/CORENAME 2>/dev/null)  MiSTer-alive: $(ps w | grep -q "[M]iSTer /media" && echo yes || echo no)"' 2>&1 | flt

say "5. uart_debug telemetry (Z/J/FC/P/RP)"
ssh -o BatchMode=yes mister 'for d in /dev/ttyS1 /dev/ttyS0; do [ -e "$d" ] || continue
    stty -F "$d" 115200 raw -echo 2>/dev/null
    line=$(timeout 6 cat "$d" 2>/dev/null | tr -d "\r" | grep -m4 .)
    [ -n "$line" ] && { echo "[$d]"; echo "$line"; break; } || echo "[$d] (no data)"; done' 2>&1 | flt | tee "$OUT/uart.txt"

say "6. probe-free framestore dump -> Mac -> objective gate"
ssh -o BatchMode=yes mister "cat > /tmp/dump_framestore.py" < "$DUMP_PY" 2>&1 | flt
ssh -o BatchMode=yes mister "python3 /tmp/dump_framestore.py 0x30000000 0xE00000 > /tmp/fs_${TAG}.bin 2>/tmp/fs_${TAG}.err; wc -c < /tmp/fs_${TAG}.bin; head -1 /tmp/fs_${TAG}.err" 2>&1 | flt
scp -q -o BatchMode=yes "mister:/tmp/fs_${TAG}.bin" "$OUT/fs_${TAG}.bin" 2>&1 | flt
echo "pulled: $(wc -c < "$OUT/fs_${TAG}.bin" 2>/dev/null) bytes -> $OUT/fs_${TAG}.bin"

say "6b. RUN THE GATE"
bash "$GATE" "$OUT/fs_${TAG}.bin" --out-dir "$OUT/render" --log-cmd 2>&1 | tee "$OUT/gate.txt"

say "7. (bonus) HDMI OSD screenshot for scanout triangulation"
ssh -o BatchMode=yes mister 'echo "screenshot" > /dev/MiSTer_cmd 2>/dev/null; sleep 2; ls -t /media/fat/screenshots/*/*.png 2>/dev/null | head -1' 2>&1 | flt | tee "$OUT/shot_path.txt"

say "DONE — artifacts in $OUT ; VERDICT is in gate.txt (PASS/FAIL). Interpret + claim upstream."
