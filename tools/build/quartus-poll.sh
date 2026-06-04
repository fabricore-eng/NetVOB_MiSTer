#!/usr/bin/env bash
# tools/build/quartus-poll.sh — snapshot the state of the detached Quartus build on
# the x86 build box (stage, elapsed, warnings/errors, whether the .rbf exists yet).
# Read-only; safe to run repeatedly. Pairs with quartus-build.sh.
#
#   Usage: tools/build/quartus-poll.sh [REVISION]
set -uo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
REV="${1:-mpeg2fpga}"
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }
BOX="${BUILDBOX_HOST:-dell.local}"; USER_AT="${BUILDBOX_USER:+${BUILDBOX_USER}@}"
KEY_OPT=""; [ -n "${BUILDBOX_SSH_KEY:-}" ] && KEY_OPT="-i ${BUILDBOX_SSH_KEY/#\~/$HOME}"
LOG="/tmp/${REV}-build.log"
RDIR="netvob/build/MiSTer_MPEG2"

ssh -o BatchMode=yes -o ConnectTimeout=10 $KEY_OPT "${USER_AT}${BOX}" "bash -s" <<EOF
echo "running: \$(pgrep -fc quartus_ >/dev/null && pgrep -fc quartus_ || echo 0) quartus procs, \$(docker ps -q | wc -l | tr -d ' ') container(s)"
echo "stage   : \$(grep -oE 'Running Quartus Prime [A-Za-z ]+' $LOG 2>/dev/null | tail -1)"
echo "errors  : \$(grep -cE '^Error' $LOG 2>/dev/null) | criticals: \$(grep -ciE 'Critical Warning' $LOG 2>/dev/null)"
echo "last    : \$(tail -1 $LOG 2>/dev/null)"
echo "rbf?    : \$(ls -la ~/$RDIR/output_files/*.rbf ~/$RDIR/*.rbf 2>/dev/null | awk '{print \$5, \$NF}' || echo 'not yet')"
EOF
