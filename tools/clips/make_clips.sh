#!/usr/bin/env bash
#
# make_clips.sh — synthesize canned MPEG-2 PROGRAM STREAM test clips for bring-up.
#
# Produces two known-good clips under tools/clips/out/ (gitignored):
#   - test480i.mpg : 720x480 INTERLACED (480i), MPEG-2, NTSC 29.97, 24-bit-equiv RGB
#                    source rasterized to 4:2:0; carries interlace + field-order flags.
#   - test480p.mpg : 720x480 PROGRESSIVE (480p), MPEG-2, 59.94 progressive.
#
# Both are wrapped as MPEG-2 PROGRAM STREAMS (`-f vob`), which is the exact payload
# the FPGA decoder ingests (DVD .VOB == MPEG-2 PS). See docs/dev-workflow.md and the
# injection-seam discussion in CLAUDE.md.
#
# The script is idempotent (regenerates on every run) and parameterized by duration.
#
# Usage:
#   tools/clips/make_clips.sh [DURATION_SECONDS]
#   DURATION=3 tools/clips/make_clips.sh
#
# After generating, each clip is VERIFIED with ffprobe:
#   - codec_name == mpeg2video
#   - container format is mpeg/program-stream
#   - for 480i: interlaced frame + field_order present
# The ffprobe summary is printed for each clip.

set -euo pipefail

# --- locate paths relative to this script (cwd-independent) -------------------
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT_DIR="${SCRIPT_DIR}/out"
mkdir -p "${OUT_DIR}"

# --- parameters ---------------------------------------------------------------
# Positional arg wins, then $DURATION env, then default 2s.
DURATION="${1:-${DURATION:-2}}"
# Resolution: NTSC DVD raster (720x480).
W=720
H=480
# MPEG-2 video bitrate (DVD-ballpark; keeps clips tiny but valid).
VBITRATE="6000k"
GOP=15

I480="${OUT_DIR}/test480i.mpg"
P480="${OUT_DIR}/test480p.mpg"

for tool in ffmpeg ffprobe; do
  command -v "${tool}" >/dev/null 2>&1 || { echo "ERROR: ${tool} not found in PATH" >&2; exit 1; }
done

echo "=== make_clips.sh ==="
echo "duration : ${DURATION}s"
echo "raster   : ${W}x${H}"
echo "out dir  : ${OUT_DIR}"
echo

# ------------------------------------------------------------------------------
# 480i — INTERLACED MPEG-2 program stream.
#   testsrc2 gives moving content so the two fields actually differ (real interlace).
#   -flags +ilme+ildct : interlaced motion estimation + interlaced DCT.
#   -top 1             : top-field-first (NTSC DVD convention).
#   -field_order tt    : tag the stream top-field-first.
#   29.97 fps frames == 59.94 fields/s == 480i59.94.
# ------------------------------------------------------------------------------
echo ">>> Generating 480i (interlaced): ${I480}"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=${W}x${H}:rate=30000/1001:duration=${DURATION}" \
  -pix_fmt yuv420p \
  -c:v mpeg2video \
  -b:v "${VBITRATE}" -minrate "${VBITRATE}" -maxrate "${VBITRATE}" -bufsize 1835k \
  -g "${GOP}" \
  -flags +ilme+ildct \
  -top 1 -field_order tt \
  -r 30000/1001 \
  -f vob "${I480}"

# ------------------------------------------------------------------------------
# 480p — PROGRESSIVE MPEG-2 program stream.
#   smptehdbars: a clean static-ish test pattern is fine for progressive bring-up.
#   59.94 fps progressive.
# ------------------------------------------------------------------------------
echo ">>> Generating 480p (progressive): ${P480}"
ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "smptehdbars=size=${W}x${H}:rate=60000/1001:duration=${DURATION}" \
  -pix_fmt yuv420p \
  -c:v mpeg2video \
  -b:v "${VBITRATE}" -minrate "${VBITRATE}" -maxrate "${VBITRATE}" -bufsize 1835k \
  -g "${GOP}" \
  -r 60000/1001 \
  -f vob "${P480}"

echo

# ------------------------------------------------------------------------------
# VERIFICATION
# ------------------------------------------------------------------------------
fail=0

verify_common() {
  # $1 = file, $2 = human label
  local f="$1" label="$2"
  echo "--- verify: ${label} (${f}) ---"
  if [[ ! -s "${f}" ]]; then
    echo "  FAIL: file missing or empty"
    fail=1
    return
  fi
  local bytes
  bytes="$(wc -c < "${f}" | tr -d ' ')"
  echo "  size      : ${bytes} bytes"

  # Container format: program stream presents as 'mpeg' (libavformat 'mpeg' demuxer).
  local fmt
  fmt="$(ffprobe -v error -show_entries format=format_name -of default=nw=1:nk=1 "${f}" || true)"
  echo "  container : ${fmt}"
  case "${fmt}" in
    *mpeg*) : ;;
    *) echo "  FAIL: container is not an MPEG program stream (got '${fmt}')"; fail=1 ;;
  esac

  # Video stream codec.
  local codec
  codec="$(ffprobe -v error -select_streams v:0 \
            -show_entries stream=codec_name -of default=nw=1:nk=1 "${f}" || true)"
  echo "  codec     : ${codec}"
  if [[ "${codec}" != "mpeg2video" ]]; then
    echo "  FAIL: video codec is not mpeg2video (got '${codec}')"
    fail=1
  fi

  # Resolution / frame rate summary.
  ffprobe -v error -select_streams v:0 \
    -show_entries stream=width,height,r_frame_rate,field_order \
    -of default=nw=0 "${f}" | sed 's/^/  /'
}

verify_common "${I480}" "480i"

# Interlace-specific checks on the 480i clip.
echo "  -- interlace flags --"
# field_order from stream metadata (tt = top-field-first).
fo="$(ffprobe -v error -select_streams v:0 \
       -show_entries stream=field_order -of default=nw=1:nk=1 "${I480}" || true)"
echo "  field_order: ${fo:-<none>}"
case "${fo}" in
  tt|bb|tb|bt) : ;;  # any interlaced field order is acceptable
  *) echo "  FAIL: 480i clip has no interlaced field_order (got '${fo:-<none>}')"; fail=1 ;;
esac
# interlaced_frame flag on the decoded frames (per-frame side data).
ilf="$(ffprobe -v error -select_streams v:0 -read_intervals "%+#1" \
        -show_entries frame=interlaced_frame,top_field_first \
        -of default=nw=1 "${I480}" 2>/dev/null || true)"
echo "  frame flags:"
echo "${ilf}" | sed 's/^/    /'
if echo "${ilf}" | grep -qiE 'interlaced_frame=1|interlaced_frame=true'; then
  : # good
else
  echo "  FAIL: first frame is not flagged interlaced_frame=1"
  fail=1
fi

echo
verify_common "${P480}" "480p"
# Sanity: progressive clip should NOT be flagged interlaced.
pfo="$(ffprobe -v error -select_streams v:0 \
        -show_entries stream=field_order -of default=nw=1:nk=1 "${P480}" || true)"
echo "  field_order: ${pfo:-progressive}"

echo
if [[ "${fail}" -ne 0 ]]; then
  echo "RESULT: FAILED — see FAIL lines above" >&2
  exit 1
fi
echo "RESULT: OK — both clips are valid MPEG-2 program streams"
echo "  480i: ${I480}"
echo "  480p: ${P480}"
