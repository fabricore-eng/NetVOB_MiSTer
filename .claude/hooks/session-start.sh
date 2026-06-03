#!/usr/bin/env bash
# NetVOB_MiSTer SessionStart hook (thin wrapper).
# Non-blocking: always exits 0. Prints the prerequisite report to stdout, which Claude
# Code surfaces to the agent at session start so it immediately knows what's reachable.
# Auto-runs only in Claude Code on the web; run scripts/verify-session.sh by hand anytime.
#
# NOT registered automatically. To enable, add to .claude/settings.json:
#   { "hooks": { "SessionStart": [ { "hooks": [
#       { "type": "command", "command": "$CLAUDE_PROJECT_DIR/.claude/hooks/session-start.sh" }
#   ] } ] } }
set -uo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"
bash "$ROOT/scripts/verify-session.sh" || true
exit 0
