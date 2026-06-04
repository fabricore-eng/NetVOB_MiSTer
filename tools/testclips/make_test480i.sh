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
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OUT="${1:-${HERE}/test480i_ntsc.m2v}"
DUR="${2:-3}"

command -v ffmpeg >/dev/null || { echo "ERROR: ffmpeg not found" >&2; exit 1; }

ffmpeg -hide_banner -loglevel error -y \
  -f lavfi -i "testsrc2=size=720x480:rate=30000/1001:duration=${DUR}" \
  -c:v mpeg2video -pix_fmt yuv420p \
  -flags +ilme+ildct -top 1 -g 12 -bf 2 \
  -b:v 6000k -maxrate 9000k -minrate 0 -bufsize 1835008 \
  -profile:v 4 -level:v 8 \
  -f mpeg2video "$OUT"

# Sanity: must be an ES (first 4 bytes = 00 00 01 b3 sequence_header_code).
hdr="$(xxd -p -l 4 "$OUT")"
[ "$hdr" = "000001b3" ] || { echo "ERROR: $OUT is not an MPEG-2 ES (hdr=$hdr)" >&2; exit 1; }
sz=$(wc -c < "$OUT" | tr -d ' ')
echo "wrote $OUT (${sz} bytes) — NTSC 480i MP@ML ES, seq_header OK"
