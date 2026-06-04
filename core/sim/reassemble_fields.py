#!/usr/bin/env python3
"""reassemble_fields.py — interleave two tv_out field PPMs into one 480-line frame.

The mpeg2fpga testbench writes ONE FIELD per tv_out_NNNN.ppm when the output
raster is interlaced (it opens a new file on each v_sync). The field-parity probe
patch (core/patches/mpeg2fpga-tvout-field-parity-probe.patch) records, in each
PPM header, the sync generator's raster odd_field bit. That bit is the GEOMETRIC
field the analog raster is painting; it is phase-offset by one field from the
BT.601 content parity. EMPIRICALLY CALIBRATED in core/sim (field-discriminating
clip vs ffmpeg field=top/bottom, ~26 dB separation, reproduced):

    # field_parity 1  carries the BT.601 TOP    field (source rows 0,2,4,...)
    # field_parity 0  carries the BT.601 BOTTOM field (source rows 1,3,5,...)

(The luma plane is NOT vertically interpolated by resample -- resample_addrgen.v
sets disp_delta_y = disp_y directly for STATE_WR_Y_* -- so each field's Y lines
are a clean copy of the source field's lines; only chroma gets bilinear vertical
upsampling. That is why the parity test on luma is unambiguous.)

This tool takes two consecutive field PPMs, extracts each field's active
WxHF region (W active dots, HF active lines/field), and weaves them into a single
W x (2*HF) progressive frame placing each field's lines at their true frame rows
per parity. The result is directly comparable (PSNR) to an ffmpeg
interlaced-decode reference of the same stream.

Usage:
  reassemble_fields.py FIELD_A.ppm FIELD_B.ppm OUT_FRAME.{ppm,gray,png} \
      [--active-w 720] [--active-h 240]
The two fields MUST be opposite parity (one parity 0, one parity 1); the tool
checks this and refuses otherwise. Output frame is top-field-first by construction
(row 0 = the parity-0 field's first line), matching BT.601 525-line TFF.

Output format is chosen by extension:
  .ppm  -> P3 RGB (renderable / ffmpeg-comparable)
  .gray -> raw 8-bit Y (BT.601 luma) for psnr.py
  .png  -> 8-bit grayscale Y

Deps: Python stdlib only.
"""
import argparse
import os
import struct
import sys
import zlib


def die(msg):
    print(f"reassemble_fields: ERROR: {msg}", file=sys.stderr)
    sys.exit(1)


def parse_field_ppm(path):
    """Return (raster_w, raster_h, parity, pixels) where pixels is a flat list of
    ints length raster_w*raster_h*3 (R,G,B per pixel). parity is 0/1 or None."""
    parity = None
    with open(path) as f:
        if f.readline().strip() != "P3":
            die(f"{path}: not a P3 PPM")
        raster_w = raster_h = None
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                toks = s.lstrip("#").split()
                if len(toks) >= 2 and toks[0] == "field_parity":
                    parity = int(toks[1])
                continue
            d = s.split()
            raster_w, raster_h = int(d[0]), int(d[1])
            break
        if raster_w is None:
            die(f"{path}: no dimension line")
        nums = f.read().split()
    px = [int(x) for x in nums[: raster_w * raster_h * 3]]
    if len(px) != raster_w * raster_h * 3:
        die(f"{path}: sample count {len(px)} != {raster_w*raster_h*3} "
            f"(truncated field — pick a later, fully-written field)")
    return raster_w, raster_h, parity, px


def active_field(raster_w, raster_h, px, aw, ah):
    """Crop the top-left aw x ah active region; return list-of-rows, each row is
    a list of (r,g,b). The active picture sits at raster rows 0..ah-1; the first
    column may be a sync edge (col 0), so we take cols [w0 .. w0+aw)."""
    if ah > raster_h:
        die(f"active height {ah} > raster height {raster_h}")
    # Detect left active column: scan row ah//2 for first non-blank pixel.
    def blank(i):
        r, g, b = px[i], px[i + 1], px[i + 2]
        return (r == g == b) and (r in (0, 48))
    midrow = ah // 2
    w0 = 0
    for x in range(raster_w):
        if not blank((midrow * raster_w + x) * 3):
            w0 = x
            break
    if w0 + aw > raster_w:
        w0 = max(0, raster_w - aw)
    rows = []
    for y in range(ah):
        row = []
        base = y * raster_w
        for x in range(w0, w0 + aw):
            i = (base + x) * 3
            row.append((px[i], px[i + 1], px[i + 2]))
        rows.append(row)
    return rows


def write_ppm(path, w, h, rows_rgb):
    with open(path, "w") as f:
        f.write(f"P3\n# reassembled interlaced frame {w}x{h} (top-field-first)\n{w} {h} 255\n")
        for row in rows_rgb:
            for (r, g, b) in row:
                f.write(f"{r} {g} {b}\n")


def luma(r, g, b):
    # BT.601 luma, rounded.
    return int(round(0.299 * r + 0.587 * g + 0.114 * b))


def write_png_gray(path, w, h, ydata):
    def chunk(tag, payload):
        out = struct.pack(">I", len(payload)) + tag + payload
        return out + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    raw = bytearray()
    for y in range(h):
        raw.append(0)
        raw += bytes(ydata[y * w:(y + 1) * w])
    with open(path, "wb") as f:
        f.write(b"\x89PNG\r\n\x1a\n")
        f.write(chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)))
        f.write(chunk(b"IDAT", zlib.compress(bytes(raw), 9)))
        f.write(chunk(b"IEND", b""))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("field_a")
    ap.add_argument("field_b")
    ap.add_argument("out")
    ap.add_argument("--active-w", type=int, default=720)
    ap.add_argument("--active-h", type=int, default=240)
    args = ap.parse_args()

    wa, ha, pa, pxa = parse_field_ppm(args.field_a)
    wb, hb, pb, pxb = parse_field_ppm(args.field_b)
    if pa is None or pb is None:
        die("a field PPM has no '# field_parity' header — rebuild with the "
            "field-parity probe patch applied")
    if pa == pb:
        die(f"both fields have parity {pa}; need opposite parities to reassemble")
    aw, ah = args.active_w, args.active_h
    fa = active_field(wa, ha, pxa, aw, ah)
    fb = active_field(wb, hb, pxb, aw, ah)

    # CALIBRATED: parity 1 = TOP (frame rows 0,2,4,...); parity 0 = BOTTOM (1,3,5,...)
    top = fa if pa == 1 else fb
    bottom = fb if pa == 1 else fa
    out_h = 2 * ah
    frame = [None] * out_h
    for i in range(ah):
        frame[2 * i] = top[i]        # even output rows from TOP/parity-1 field
        frame[2 * i + 1] = bottom[i]  # odd output rows from BOTTOM/parity-0 field

    print(f"reassemble_fields: A={os.path.basename(args.field_a)} parity={pa}, "
          f"B={os.path.basename(args.field_b)} parity={pb}")
    print(f"  TOP field    = parity-1 field -> frame rows 0,2,4,...")
    print(f"  BOTTOM field = parity-0 field -> frame rows 1,3,5,...")
    print(f"  output frame = {aw}x{out_h} (top-field-first)")

    ext = os.path.splitext(args.out)[1].lower()
    if ext == ".ppm":
        write_ppm(args.out, aw, out_h, frame)
    elif ext in (".gray", ".png"):
        ydata = bytearray(aw * out_h)
        for y in range(out_h):
            for x in range(aw):
                r, g, b = frame[y][x]
                ydata[y * aw + x] = luma(r, g, b)
        if ext == ".gray":
            with open(args.out, "wb") as f:
                f.write(bytes(ydata))
        else:
            write_png_gray(args.out, aw, out_h, ydata)
    else:
        die(f"unknown output extension {ext} (use .ppm/.gray/.png)")
    print(f"  wrote {args.out}")


if __name__ == "__main__":
    main()
