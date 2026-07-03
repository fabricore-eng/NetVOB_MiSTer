#!/usr/bin/env bash
# make_test480i.sh — (re)generate the golden NTSC 480i MPEG-2 *elementary* test clip.
#
# This clip is the decode-bring-up reference used in BOTH paths:
#   - sim:  core/sim/prep_stream.sh tools/testclips/test480i_ntsc.m2v  (proven to decode)
#   - HW:   staged to mister:/media/fat/test.mpg and mounted to the S0 slot via .mgl,
#           so a black CRT then isolates to FEED/mem — NOT to a bad/missing/wrong-format clip.
#
# Why an ELEMENTARY stream (not a program stream): the mpeg2fpga VLD front-end
# (probe.v/getbits.v) consumes a *video elementary stream* — start code 0x000001B3.
# mpg_streamer feeds mounted sectors byte-for-byte into stream_data with NO PS demux,
# so the file on the board must already be ES. (Real DVD VOBs are PROGRAM streams;
# the PS->ES demux is a separate, later milestone — see docs/progress.md.)
#
# Format: 720x480, MP@ML (profile 4 / level 8), interlaced TFF, full I/P/B GOP,
# ~6 Mbps, ~3 s => ~2 MB (well under the 4 MiB sim cap and a trivial board file).
#
# Usage: tools/testclips/make_test480i.sh [OUT.m2v] [DURATION_SEC]
#   ALLI=1 tools/testclips/make_test480i.sh   -> all-INTRA variant (every frame an I-frame)
#
# The ALLI variant (default out: test480i_ntsc_allI.m2v) sets GOP=1 / no B-frames so
# EVERY frame is intra-coded. Decode-bring-up use: because every frame carries the full
# picture with no inter-prediction, ANY framestore snapshot on HW lands on a complete
# intra frame — isolating intra reconstruction (IDCT/coeff) from the motion-comp/
# reference-fetch path. If the normal GOP clip degrades to gray on HW but this all-I
# clip decodes the bars, the bug is in inter-prediction, not intra. (See docs/progress.md
# 'decode is PARTIAL/DEGRADED on HW'.)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ALLI="${ALLI:-0}"
STATIC="${STATIC:-0}"
if [ "$STATIC" = 1 ]; then
  # STATIC variant (default out: test480i_static.m2v): freeze testsrc2 frame 0 and encode
  # DUR seconds of that ONE picture (all-identical, all-intra) frames. Purpose: a PHASE-FREE
  # decode gate. A free-running HW decoder produces ~780 frames of a looping clip, so a
  # framestore snapshot lands on an ARBITRARY animation phase; against a sparse reference set
  # whose own adjacent-frame SSIM is only ~0.92, even a flawless decode can't clear 0.95. A
  # static clip removes phase entirely — every decoded frame is the same picture, so the
  # snapshot compares directly to a single reference (expect >=0.95 if decode is correct).
  # See docs/progress.md 2026-07-03 #8/#9.
  OUT="${1:-${HERE}/test480i_static.m2v}"; GOP=1; BF=0
elif [ "$ALLI" = 1 ]; then
  OUT="${1:-${HERE}/test480i_ntsc_allI.m2v}"; GOP=1; BF=0
else
  OUT="${1:-${HERE}/test480i_ntsc.m2v}"; GOP=12; BF=2
fi
DUR="${2:-3}"

command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found" >&2; exit 1; }

if [ "$STATIC" = 1 ]; then
  STILL="$(mktemp -t still480.XXXXXX).png"
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=720x480:rate=30000/1001" -frames:v 1 "$STILL"
  ffmpeg -hide_banner -loglevel error -y \
    -loop 1 -i "$STILL" -t "$DUR" -r 30000/1001 \
    -c:v mpeg2video -pix_fmt yuv420p \
    -flags +ilme+ildct -top 1 -g "$GOP" -bf "$BF" \
    -b:v 6000k -maxrate 9000k -minrate 0 -bufsize 1835008 \
    -profile:v 4 -level:v 8 \
    -f mpeg2video "$OUT"
  rm -f "$STILL"
else
  ffmpeg -hide_banner -loglevel error -y \
    -f lavfi -i "testsrc2=size=720x480:rate=30000/1001:duration=${DUR}" \
    -c:v mpeg2video -pix_fmt yuv420p \
    -flags +ilme+ildct -top 1 -g "$GOP" -bf "$BF" \
    -b:v 6000k -maxrate 9000k -minrate 0 -bufsize 1835008 \
    -profile:v 4 -level:v 8 \
    -f mpeg2video "$OUT"
fi

# Sanity: must be an ES (first 4 bytes = 00 00 01 b3 sequence_header_code).
hdr="$(xxd -p -l 4 "$OUT")"
[ "$hdr" = "000001b3" ] || { echo "ERROR: $OUT is not an MPEG-2 ES (hdr=$hdr)" >&2; exit 1; }
sz=$(wc -c < "$OUT" | tr -d ' ')
echo "wrote $OUT (${sz} bytes) — NTSC 480i MP@ML ES, seq_header OK"
