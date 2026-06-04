#!/usr/bin/env bash
# prep_stream.sh — build stream.dat (the $readmemh hex input) the testbench expects.
#
# The upstream bench/iverilog/Makefile does this with GNU-specific tools
# (`head --bytes=4m`, `xxd -c 1 | cut`). This is a macOS/BSD-portable equivalent.
#
# Usage: ./prep_stream.sh [SOURCE.mpg] [MAX_BYTES] [OUT.dat]
#   SOURCE.mpg : MPEG-2 elementary stream (.m2v / program stream).
#                Default: the shipped greyramp.mpg (720x576 PAL interlaced, MP@ML).
#   MAX_BYTES  : truncate the source to at most this many bytes (testbench cap
#                is `MAX_STREAM_LENGTH` = 4 MiB). Default: 4194304.
#   OUT.dat    : output hex file. Default: ./stream.dat (next to this script).
#
# stream.dat format: one hex byte per line (what $readmemh wants), with the
# repo's end-of-sequence.mpg (a run of 0x000001b7 sequence_end_codes) appended
# so the decoder cleanly flushes the last picture.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MPEG2="${HERE}/../mpeg2fpga"
SRC="${1:-${MPEG2}/tools/streams/greyramp.mpg}"
MAX="${2:-4194304}"
OUT="${3:-${HERE}/stream.dat}"
EOS="${MPEG2}/tools/streams/end-of-sequence.mpg"

[ -f "$SRC" ] || { echo "ERROR: source stream not found: $SRC" >&2; exit 1; }
[ -f "$EOS" ] || { echo "ERROR: end-of-sequence.mpg not found: $EOS" >&2; exit 1; }

# Truncate source to MAX bytes, emit one hex byte per line, then append EOS.
# `xxd -p -c 1` => two hex digits per line (lowercase). $readmemh accepts that.
{
  head -c "$MAX" "$SRC"
  cat "$EOS"
} | xxd -p -c 1 > "$OUT"

LINES=$(wc -l < "$OUT" | tr -d ' ')
echo "wrote $OUT ($LINES bytes) from $(basename "$SRC") (cap ${MAX}B) + end-of-sequence"
