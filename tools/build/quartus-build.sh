#!/usr/bin/env bash
# tools/build/quartus-build.sh — stage a MiSTer Quartus project to the x86 build box
# and launch a DETACHED Quartus compile (so a laptop sleep/SIGHUP can't kill the
# ~30-min build). Implements docs/dev-workflow.md §4.
#
#   Usage: tools/build/quartus-build.sh [PROJECT_DIR] [REVISION]
#     PROJECT_DIR : path to the Quartus project (default: core/MiSTer_MPEG2)
#     REVISION    : Quartus revision name (default: mpeg2fpga)
#
#   Reads BUILDBOX_HOST/USER/SSH_KEY from .env (or the `dell` ssh-config alias).
#   Stages source-only (excludes the stale db/, output_files/, sim artifacts) to a
#   clean dir on the build box, then runs raetro/quartus:17.0 detached, logging to
#   the box's /tmp/<rev>-build.log. Prints the PID + how to poll. Does NOT block.
#
#   Poll:   tools/build/quartus-poll.sh        (tails stage/elapsed/warnings/.rbf)
set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PROJECT_DIR="${1:-$ROOT/core/MiSTer_MPEG2}"
REV="${2:-mpeg2fpga}"
[ -f "$ROOT/.env" ] && { set -a; . "$ROOT/.env"; set +a; }

# Prefer the ssh-config alias if BUILDBOX_HOST is the dell; fall back to explicit.
BOX="${BUILDBOX_HOST:-dell.local}"
USER_AT="${BUILDBOX_USER:+${BUILDBOX_USER}@}"
KEY_OPT=""; [ -n "${BUILDBOX_SSH_KEY:-}" ] && KEY_OPT="-i ${BUILDBOX_SSH_KEY/#\~/$HOME}"
SSH=(ssh -o BatchMode=yes -o ConnectTimeout=10 $KEY_OPT "${USER_AT}${BOX}")
RSYNC_E="ssh -o BatchMode=yes $KEY_OPT"

REMOTE_DIR="netvob/build/$(basename "$PROJECT_DIR")"
IMG="raetro/quartus:17.0"
LOG="/tmp/${REV}-build.log"

echo "── quartus-build ───────────────────────────────────────────"
echo "  project : $PROJECT_DIR  (revision: $REV)"
echo "  box     : ${USER_AT}${BOX}:~/$REMOTE_DIR"
echo "  image   : $IMG   log: $LOG"

[ -f "$PROJECT_DIR/$REV.qpf" ] || { echo "ERROR: $PROJECT_DIR/$REV.qpf not found"; exit 1; }

echo "=== staging source (excludes stale db/, output_files/, sim artifacts) ==="
"${SSH[@]}" "mkdir -p ~/$REMOTE_DIR" || { echo "ERROR: could not create remote dir"; exit 1; }
rsync -az --delete -e "$RSYNC_E" \
  --exclude 'db/' --exclude 'incremental_db/' --exclude 'output_files/' \
  --exclude '*.qws' --exclude '*.rpt' --exclude '.git' --exclude '.DS_Store' \
  --exclude 'simulation/' --exclude '*.vcd' --exclude '*.vvp' \
  --exclude 'mpeg2_sim' --exclude 'sim_mpg' --exclude 'build.log' \
  "$PROJECT_DIR"/ "${USER_AT}${BOX}:$REMOTE_DIR/" || { echo "ERROR: rsync failed"; exit 1; }

echo "=== launching DETACHED compile (survives SIGHUP) ==="
# setsid+nohup so it outlives the SSH session; docker mounts the staged dir as /work.
"${SSH[@]}" "cd ~/$REMOTE_DIR && \
  setsid nohup docker run --rm -v \"\$PWD\":/work -w /work --entrypoint quartus_sh \
    $IMG --flow compile $REV > $LOG 2>&1 </dev/null & \
  echo \"build launched; host PID \$!\"; sleep 3; \
  echo '--- first log lines ---'; head -8 $LOG 2>/dev/null || echo '(log not yet written)'"

echo "────────────────────────────────────────────────────────────"
echo "Poll with: tools/build/quartus-poll.sh   (or: ssh ${BOX} tail -f $LOG)"
