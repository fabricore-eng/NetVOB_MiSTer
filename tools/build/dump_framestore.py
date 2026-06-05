#!/usr/bin/env python3
"""dump_framestore.py — RUNS ON THE MiSTer (it has /usr/bin/python3).

Camera-free decode verification: read the decoder's framestore straight out of the
FPGA-reserved DDR window and stream the raw bytes to stdout, so the Mac can render the
decoded Y planes as PNGs (proving the decoder wrote a correct IMAGE to DDR, independent
of the analog/scaler display path).

The mpeg2fpga decoder writes decoded frames into the HPS window-3 region: mem_shim.sv
maps decoder word address -> {7'b0011000, addr[21:0]}, i.e. physical byte address
= 0x30000000 + word_addr*8. The Y plane is row-major contiguous (per the bench's
write_mb): 2*mb_width words/row (90 = 720px) x 16*mb_height rows (480). We dump a
contiguous span covering all four FRAME_n buffers; the Mac side slices + renders.

Coherency: the FPGA writes via f2sdram (non-coherent with the ARM caches), so /dev/mem
is opened O_SYNC and mmap'd MAP_SHARED to read uncached — otherwise an ARM read can
return stale cache lines (plausible-but-wrong pixels). Falls back to os.pread if mmap
is refused.

Usage (on the board, or via ssh):
  python3 dump_framestore.py [BASE_HEX] [LEN_HEX] > fs.bin
  defaults: BASE=0x30000000  LEN=0xE00000 (14 MiB — covers FRAME_0..3 Y+chroma + OSD)
"""
import mmap
import os
import sys

BASE = int(sys.argv[1], 16) if len(sys.argv) > 1 else 0x30000000
LEN = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x00E00000

PAGE = 4096
assert BASE % PAGE == 0, "BASE must be page-aligned"
if LEN % PAGE:
    LEN += PAGE - (LEN % PAGE)

out = sys.stdout.buffer
fd = os.open("/dev/mem", os.O_RDWR | os.O_SYNC)
try:
    try:
        m = mmap.mmap(fd, LEN, mmap.MAP_SHARED, mmap.PROT_READ, offset=BASE)
        # Copy out in chunks so a huge span doesn't balloon memory.
        step = 1 << 20
        for off in range(0, LEN, step):
            out.write(m[off:min(off + step, LEN)])
        m.close()
        sys.stderr.write(f"dump_framestore: mmap OK, {LEN} bytes from {hex(BASE)}\n")
    except (OSError, ValueError) as e:
        # Fallback: positional read (less certain on coherency, but better than nothing).
        sys.stderr.write(f"dump_framestore: mmap failed ({e}); falling back to pread\n")
        step = 1 << 20
        for off in range(0, LEN, step):
            chunk = os.pread(fd, min(step, LEN - off), BASE + off)
            if not chunk:
                break
            out.write(chunk)
finally:
    os.close(fd)
    out.flush()
