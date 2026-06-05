#!/usr/bin/env python3
"""render_framestore.py — RUNS ON THE MAC. Render decoded Y planes from a raw DDR dump.

Companion to dump_framestore.py. Takes the raw bytes dumped from the FPGA DDR window
(base 0x30000000) and reconstructs the four FRAME_n luma planes as 720x480 grayscale
PNGs — camera-free proof that the decoder wrote a correct IMAGE to DDR.

Layout (derived + validated against the mpeg2fpga bench write_mb/write_row,
core/mpeg2fpga/bench/iverilog/mem_ctl.v):
  * Y plane is ROW-MAJOR CONTIGUOUS from the frame's Y base: 2*mb_width words/row
    (=90 -> 720 px), 16*mb_height rows (=480). word_addr = base + row*90 + wordcol.
  * physical byte addr = 0x30000000 + word_addr*8  (mem_shim {7'b0011000, addr}).
  * write_row unpacks {pixel_0..pixel_7} = mem[addr] with pixel_0 = bits[63:56] (MSB).
    On little-endian readback the 8 in-memory bytes are therefore pixel_7..pixel_0 ->
    we REVERSE each 8-byte word to get left-to-right pixel order.
  * Samples are stored SIGNED (I-frame mean 128 in sim) -> display = (s+128)&0xFF,
    i.e. XOR 0x80. Toggle with --no-bias if a plane looks inverted.

The display buffer rotates across FRAME_0..3 and the live index isn't exported, so we
render ALL FOUR and print stats; the real frame is whichever is non-flat/coherent.

Usage:
  render_framestore.py fs.bin OUTDIR [--base 0x30000000] [--w 720] [--h 480]
                        [--no-flip] [--no-bias]
"""
import argparse
import os
import struct
import sys
import zlib

# FRAME_n Y-plane base WORD addresses within the 0x30000000 window (decoder word addrs;
# plan-corrected MP@HL spacing 0x60000 words = 3 MiB apart). Rendering is offset-driven,
# so if a frame looks shifted these are the first knobs to adjust.
FRAME_Y_WORD = [0x000000, 0x060000, 0x0C0000, 0x120000]


def write_png_gray(path, w, h, data):
    def chunk(tag, payload):
        out = struct.pack(">I", len(payload)) + tag + payload
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return out + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # grayscale
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += data[y * w:(y + 1) * w]
    with open(path, "wb") as f:
        f.write(sig)
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def render_plane(buf, byte_base, w, h, flip, bias):
    """Extract a w*h Y plane starting at byte_base in buf."""
    words_per_row = w // 8
    plane = bytearray(w * h)
    xor = 0x80 if bias else 0x00
    for row in range(h):
        roff = byte_base + row * words_per_row * 8
        if roff + w > len(buf):
            break
        doff = row * w
        for wc in range(words_per_row):
            word = buf[roff + wc * 8: roff + wc * 8 + 8]
            if flip:
                word = word[::-1]  # mem bytes are pixel_7..pixel_0 -> reverse
            for k in range(8):
                plane[doff + wc * 8 + k] = (word[k] ^ xor) & 0xFF
    return plane


def stats(plane):
    n = len(plane)
    if not n:
        return 0.0, 0.0, 0.0
    mean = sum(plane) / n
    nz = 100.0 * sum(1 for b in plane if b > 16) / n
    var = sum((b - mean) ** 2 for b in plane) / n
    return mean, nz, var ** 0.5


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    ap.add_argument("outdir")
    ap.add_argument("--base", default="0x30000000")
    ap.add_argument("--w", type=int, default=720)
    ap.add_argument("--h", type=int, default=480)
    ap.add_argument("--no-flip", action="store_true")
    ap.add_argument("--no-bias", action="store_true")
    args = ap.parse_args()

    buf = open(args.dump, "rb").read()
    os.makedirs(args.outdir, exist_ok=True)
    flip = not args.no_flip
    bias = not args.no_bias
    print(f"render_framestore: {len(buf)} bytes, {args.w}x{args.h}, "
          f"flip={flip} bias={bias}")
    for n, wbase in enumerate(FRAME_Y_WORD):
        byte_base = wbase * 8  # offset within the dump (dump starts at window base)
        plane = render_plane(buf, byte_base, args.w, args.h, flip, bias)
        mean, nz, sd = stats(plane)
        png = os.path.join(args.outdir, f"frame{n}_Y.png")
        write_png_gray(png, args.w, args.h, bytes(plane))
        flag = "  <-- looks like real image" if (sd > 8 and 5 < mean < 250) else ""
        print(f"  FRAME_{n} @word {hex(wbase)}: mean={mean:.1f} nonzero%={nz:.1f} "
              f"stddev={sd:.1f} -> {png}{flag}")


if __name__ == "__main__":
    main()
