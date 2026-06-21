#!/usr/bin/env bash
# hw_settle_dump.sh — flash current rbf (already on /media/fat) + SETTLE-CHECKED framestore dump.
# Rules out dump-timing: dumps twice ~13s apart, reports per-MB-row structure of both + whether
# they match (settled). Warm-reboot-first (clean HPS bridge). Lock-gated. Loads core from the
# staged /media/fat/mpeg2fpga_dvd.rbf + /media/fat/mpeg2_test.mgl (must already be staged).
# Usage: tools/build/hw_settle_dump.sh [TAG]
set -uo pipefail
TAG="${1:-settle}"
HUB="$HOME/Dev/tools"
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
DUMP_PY="$ROOT/tools/build/dump_framestore.py"
OUT="/tmp/hw_settle_${TAG}"; mkdir -p "$OUT"
flt(){ grep -vE 'post-quantum|store now|openssh|vulnerable|server may|upgraded'; }
say(){ printf '\n── %s\n' "$*"; }

say "acquire + warm-reboot (clean bridge)"
"$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "LOCKED — abort"; exit 1; }
cleanup(){ ssh -o BatchMode=yes mister 'echo "load_core /media/fat/menu.rbf" > /dev/MiSTer_cmd' 2>&1|flt; "$HUB/tools/dell_coord.sh" devlock mister release dvd 2>&1|flt; }
trap cleanup EXIT
"$HUB/tools/dell_coord.sh" devlock mister reboot dvd 2>&1 | flt || { echo "reboot refused — abort"; exit 1; }
up=0; for i in $(seq 1 36); do ssh -o BatchMode=yes -o ConnectTimeout=4 mister 'test -e /dev/MiSTer_cmd' 2>/dev/null && { up=1; echo "up after ~$((i*5))s"; break; }; sleep 5; done
[ "$up" = 1 ] || { echo "no comeback — abort"; exit 2; }
sleep 5; "$HUB/tools/dell_coord.sh" devlock mister acquire dvd 2>&1 | flt || { echo "re-acquire failed — abort"; exit 1; }

say "load_core (staged rbf + mgl)"
ssh -o BatchMode=yes mister 'timeout 12 bash -c "echo load_core /media/fat/mpeg2_test.mgl > /dev/MiSTer_cmd" && echo sent; sleep 9
  echo "CORENAME: $(cat /tmp/CORENAME 2>/dev/null)"' 2>&1 | flt
ssh -o BatchMode=yes mister "cat > /tmp/dump_framestore.py" < "$DUMP_PY" 2>&1 | flt

for n in 1 2; do
  say "dump #$n (after settle)"
  ssh -o BatchMode=yes mister "python3 /tmp/dump_framestore.py 0x30000000 0xE00000 > /tmp/fs_${TAG}_$n.bin 2>/dev/null; wc -c < /tmp/fs_${TAG}_$n.bin" 2>&1 | flt
  scp -q -o BatchMode=yes "mister:/tmp/fs_${TAG}_$n.bin" "$OUT/fs_${TAG}_$n.bin" 2>&1 | flt
  [ "$n" = 1 ] && { echo "...settling 13s..."; sleep 13; }
done

say "ANALYSIS (per-MB-row !=128, both dumps + match)"
python3 - "$OUT/fs_${TAG}_1.bin" "$OUT/fs_${TAG}_2.bin" <<'PY'
import sys,numpy as np
def f0(p):
    raw=np.fromfile(p,dtype=np.uint8,count=345600)
    return raw.reshape(-1,8)[:,::-1].reshape(480,720)
a,b=f0(sys.argv[1]),f0(sys.argv[2])
for nm,f in [('dump1',a),('dump2',b)]:
    rows=[f[i*16:(i+1)*16] for i in range(30)]
    dec=[100*(r!=128).mean() for r in rows]
    print(f'{nm}: !=128 overall {100*(f!=128).mean():.1f}%  per-MB-row(%!=128): '+' '.join(f'{d:.0f}' for d in dec))
print(f'SETTLED (dump1==dump2 in FRAME_0 Y): {bool((a==b).all())}')
PY
say "DONE — bins in $OUT"
