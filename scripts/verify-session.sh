#!/usr/bin/env bash
# NetVOB_MiSTer — session prerequisite verifier.
# Prints a ✅/⚠️/❌ report of what an autonomous session can reach. NEVER fails (exit 0),
# so it is safe as a non-blocking SessionStart hook and runnable by hand anytime.
#   What to stage:  docs/session-bootstrap.md
#   Run-loop:       docs/autonomy.md
set -uo pipefail

ROOT="${CLAUDE_PROJECT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
cd "$ROOT" 2>/dev/null || true
# shellcheck disable=SC1091
[ -f .env ] && { set -a; . ./.env; set +a; } || true

pass=0; warn=0; fail=0
ok(){ echo "  ✅ $1"; pass=$((pass+1)); }
wn(){ echo "  ⚠️  $1"; warn=$((warn+1)); }
no(){ echo "  ❌ $1"; fail=$((fail+1)); }

ssh_args(){ # KEY -> echoes ssh options
  local key="${1:-}"
  printf '%s ' -o BatchMode=yes -o ConnectTimeout=5 -o StrictHostKeyChecking=accept-new
  [ -n "$key" ] && printf '%s ' -i "$key"
}
ssh_try(){ # label HOST USER KEY
  local label="$1" host="${2:-}" user="${3:-}" key="${4:-}"
  [ -z "$host" ] && { wn "$label not configured (.env)"; return; }
  local tgt="${user:+$user@}$host"
  # shellcheck disable=SC2046
  if ssh $(ssh_args "$key") "$tgt" true 2>/dev/null; then
    ok "$label reachable ($tgt)"
  else
    no "$label unreachable ($tgt) — check network policy / SSH key"
  fi
}

echo "── NetVOB_MiSTer session preflight ─────────────────────────────"

echo "Toolchain (in-container):"
for t in git ffmpeg verilator python3; do
  if command -v "$t" >/dev/null 2>&1; then ok "$t ($("$t" --version 2>/dev/null | head -1))"
  else wn "$t not installed (agent can apt/brew it)"; fi
done

echo "Secrets (.env):"
if [ -f .env ]; then
  ok ".env present"
  for k in MISTER_HOST PI_HOST BUILDBOX_HOST PLEX_URL PLEX_TOKEN TMDB_API_KEY DVD_DUMPS_DIR; do
    if [ -n "${!k:-}" ]; then ok "$k set"; else wn "$k empty"; fi
  done
else
  no ".env missing — copy .env.example → .env and fill it (docs/session-bootstrap.md)"
fi

echo "Hosts (network policy must permit these):"
ssh_try "FPGA build box"        "${BUILDBOX_HOST:-}" "${BUILDBOX_USER:-}" "${BUILDBOX_SSH_KEY:-}"
ssh_try "SuperStation (mister)" "${MISTER_HOST:-}"   "${MISTER_USER:-}"   "${MISTER_SSH_KEY:-}"
ssh_try "Raspberry Pi 5"        "${PI_HOST:-}"       "${PI_USER:-}"       "${PI_SSH_KEY:-}"

echo "Plex (PlexSource):"
if [ -n "${PLEX_URL:-}" ] && [ -n "${PLEX_TOKEN:-}" ]; then
  if command -v curl >/dev/null 2>&1 && \
     curl -fsS -m 5 -H "X-Plex-Token: ${PLEX_TOKEN}" "${PLEX_URL%/}/identity" >/dev/null 2>&1; then
    ok "Plex reachable ($PLEX_URL)"
  else no "Plex not reachable ($PLEX_URL) — check policy / token"; fi
else wn "Plex not configured (PLEX_URL / PLEX_TOKEN) → PlexSource = stub"; fi

echo "DVD test dumps:"
if [ -n "${PI_HOST:-}" ] && [ -n "${DVD_DUMPS_DIR:-}" ]; then
  # shellcheck disable=SC2046
  if ssh $(ssh_args "${PI_SSH_KEY:-}") "${PI_USER:+$PI_USER@}$PI_HOST" "test -d '${DVD_DUMPS_DIR}'" 2>/dev/null; then
    ok "dumps dir present on Pi ($DVD_DUMPS_DIR)"
  else no "dumps dir missing on Pi ($DVD_DUMPS_DIR) — stage VOB/VIDEO_TS test titles"; fi
else wn "DVD dumps location not configured (PI_HOST / DVD_DUMPS_DIR)"; fi

echo "Vendored submodules (pinned SHAs):"
if [ -f .gitmodules ]; then
  ok ".gitmodules present"; git submodule status 2>/dev/null | sed 's/^/    /'
else wn "no submodules yet (added at M0: MiSTer_MPEG2, Template_MiSTer sys/, CDi_MiSTer, …)"; fi

echo "────────────────────────────────────────────────────────────────"
echo "Preflight: ${pass} ok / ${warn} warn / ${fail} blocked."
if [ "$fail" -gt 0 ]; then
  echo "Blocked items limit hardware/build/Plex work (see docs/session-bootstrap.md)."
  echo "Sim (Verilator conformance→PNG) + Pi-service workstreams may still proceed (docs/autonomy.md)."
fi
exit 0
