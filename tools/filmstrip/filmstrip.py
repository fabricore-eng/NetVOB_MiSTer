#!/usr/bin/env python3
"""filmstrip.py — stitch N evenly-spaced frames into one contact-sheet PNG.

This is the software analogue of dev-workflow.md's *filmstrip* hardware-verification
rung: a single frame never catches playback/seek/field behavior, so we sample a
window of frames and lay them out as a grid for a quick visual scan.

Two input modes:
  1. A video clip (e.g. tools/clips/out/test480i.mpg) — frames are sampled with
     ffmpeg at N evenly-spaced timestamps across the clip's duration.
  2. A directory of frame PNGs (e.g. a Verilator sim trace dump, or screenshots
     pulled back from hardware) — N are picked evenly across the sorted set.

Dependencies: ffmpeg / ffprobe + Python stdlib ONLY (no pip).

Usage:
  filmstrip.py INPUT [-n N] [-o OUT.png] [--cols C] [--label]
    INPUT      a video file OR a directory of PNGs
    -n / --frames   number of frames to sample (default 8)
    -o / --output   output PNG path (default <input>.filmstrip.png or out/strip.png)
    --cols          columns in the grid (default: ceil(sqrt(n)))
    --label         burn a small timestamp/index label onto each cell
    --gap           pixel gap between cells (default 4)

Exit status is non-zero on any failure.
"""

import argparse
import glob
import math
import os
import shutil
import struct
import subprocess
import sys
import tempfile
import zlib


def die(msg, code=1):
    print(f"filmstrip: ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def need(tool):
    if shutil.which(tool) is None:
        die(f"required tool '{tool}' not found in PATH")


# ---------------------------------------------------------------------------
# Minimal PNG reader/writer (stdlib only — no PIL).
# We read frames as raw RGBA via ffmpeg, so the "reader" is really just
# the ffmpeg rawvideo pipe below; the writer emits a single RGBA PNG.
# ---------------------------------------------------------------------------
def write_png_rgba(path, width, height, pixels):
    """pixels: bytes, length width*height*4, RGBA row-major top-to-bottom."""
    def chunk(tag, data):
        out = struct.pack(">I", len(data)) + tag + data
        crc = zlib.crc32(tag + data) & 0xFFFFFFFF
        return out + struct.pack(">I", crc)

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    # Add filter byte 0 at the start of each scanline.
    stride = width * 4
    raw = bytearray()
    for y in range(height):
        raw.append(0)
        raw += pixels[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 9)
    with open(path, "wb") as f:
        f.write(sig)
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", idat))
        f.write(chunk(b"IEND", b""))


def ffprobe_float(path, entry):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", entry,
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    try:
        v = float(out.stdout.strip().splitlines()[0])
        # ffprobe prints 'N/A' -> ValueError above; guard against nan/inf too.
        return v if v == v else None
    except (ValueError, IndexError):
        return None


def ffprobe_duration(path):
    return ffprobe_float(path, "format=duration")


def ffprobe_wh(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height",
         "-of", "default=nw=1:nk=1", path],
        capture_output=True, text=True,
    )
    nums = [int(t) for t in out.stdout.split() if t.strip().isdigit()]
    if len(nums) >= 2:
        return nums[0], nums[1]
    return None, None


def extract_frame_rgba(src, timestamp, w, h):
    """Decode one frame at `timestamp` (seconds, relative to stream start) -> raw RGBA.

    Uses OUTPUT seeking (`-ss` AFTER `-i`): decode from the start, then grab the
    frame at the offset. Raw MPEG program streams have no seek index, so INPUT
    seeking (`-ss` before `-i`) overshoots/fails near EOF. For the short test
    clips this targets, decode-then-seek is both reliable and fast enough.
    `timestamp` here is relative to the stream's own start (0 == first frame).
    """
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", src,
        "-ss", f"{timestamp:.3f}",
        "-frames:v", "1",
        "-vf", f"scale={w}:{h}:flags=bicubic",
        "-pix_fmt", "rgba", "-f", "rawvideo", "-",
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or len(res.stdout) != w * h * 4:
        return None
    return res.stdout


def png_to_rgba(src, w, h):
    """Use ffmpeg to read a PNG (any format) and re-emit as scaled raw RGBA."""
    cmd = [
        "ffmpeg", "-hide_banner", "-loglevel", "error",
        "-i", src, "-frames:v", "1",
        "-vf", f"scale={w}:{h}:flags=bicubic",
        "-pix_fmt", "rgba", "-f", "rawvideo", "-",
    ]
    res = subprocess.run(cmd, capture_output=True)
    if res.returncode != 0 or len(res.stdout) != w * h * 4:
        return None
    return res.stdout


# ---------------------------------------------------------------------------
# Tiny 5x7 bitmap font for optional cell labels (digits, a few symbols).
# Keeps us PIL-free. Only chars we need: 0-9 . : t = s f i F #  space
# ---------------------------------------------------------------------------
_FONT = {
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"],
    "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"],
    "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"],
    "5": ["11111", "10000", "11110", "00001", "00001", "10001", "01110"],
    "6": ["00110", "01000", "10000", "11110", "10001", "10001", "01110"],
    "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"],
    "9": ["01110", "10001", "10001", "01111", "00001", "00010", "01100"],
    ".": ["00000", "00000", "00000", "00000", "00000", "01100", "01100"],
    ":": ["00000", "01100", "01100", "00000", "01100", "01100", "00000"],
    "=": ["00000", "00000", "11111", "00000", "11111", "00000", "00000"],
    "s": ["00000", "00000", "01111", "10000", "01110", "00001", "11110"],
    "t": ["01000", "01000", "11110", "01000", "01000", "01001", "00110"],
    "f": ["00110", "01001", "01000", "11110", "01000", "01000", "01000"],
    "i": ["00100", "00000", "01100", "00100", "00100", "00100", "01110"],
    "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "#": ["01010", "01010", "11111", "01010", "11111", "01010", "01010"],
    " ": ["00000", "00000", "00000", "00000", "00000", "00000", "00000"],
}


def draw_label(buf, bw, bh, x0, y0, text, scale=2):
    """Draw `text` into RGBA buffer `buf` (bytearray) at (x0,y0), white on dark box."""
    cw, ch = 5, 7
    pad = 1
    tw = len(text) * (cw + 1) * scale + 2 * pad
    th = ch * scale + 2 * pad
    # Dark background box for legibility.
    for yy in range(y0, min(y0 + th, bh)):
        for xx in range(x0, min(x0 + tw, bw)):
            i = (yy * bw + xx) * 4
            buf[i] = buf[i] // 4
            buf[i + 1] = buf[i + 1] // 4
            buf[i + 2] = buf[i + 2] // 4
            buf[i + 3] = 255
    cx = x0 + pad
    for ch_ in text:
        glyph = _FONT.get(ch_, _FONT[" "])
        for ry, row in enumerate(glyph):
            for rx, bit in enumerate(row):
                if bit == "1":
                    for sy in range(scale):
                        for sx in range(scale):
                            px = cx + rx * scale + sx
                            py = y0 + pad + ry * scale + sy
                            if 0 <= px < bw and 0 <= py < bh:
                                i = (py * bw + px) * 4
                                buf[i] = 255
                                buf[i + 1] = 255
                                buf[i + 2] = 255
                                buf[i + 3] = 255
        cx += (cw + 1) * scale


def main():
    ap = argparse.ArgumentParser(description="Stitch N frames into a contact-sheet PNG.")
    ap.add_argument("input", help="video file OR directory of PNG frames")
    ap.add_argument("-n", "--frames", type=int, default=8, help="frames to sample (default 8)")
    ap.add_argument("-o", "--output", default=None, help="output PNG path")
    ap.add_argument("--cols", type=int, default=0, help="grid columns (default ~sqrt(n))")
    ap.add_argument("--cell-width", type=int, default=320, help="per-cell width px (default 320)")
    ap.add_argument("--gap", type=int, default=4, help="gap between cells px (default 4)")
    ap.add_argument("--label", action="store_true", help="burn timestamp/index labels")
    args = ap.parse_args()

    need("ffmpeg")
    need("ffprobe")

    n = max(1, args.frames)
    src = args.input
    if not os.path.exists(src):
        die(f"input not found: {src}")

    # Gather (cell_rgba_source, label) descriptors.
    # We compute target cell dimensions from the first real frame's aspect ratio.
    cells = []  # list of (raw_rgba_bytes, label)

    if os.path.isdir(src):
        pngs = sorted(glob.glob(os.path.join(src, "*.png")) +
                      glob.glob(os.path.join(src, "*.PNG")))
        if not pngs:
            die(f"no PNG frames found in directory: {src}")
        # Pick n evenly across the sorted set.
        if len(pngs) <= n:
            picks = list(enumerate(pngs))
        else:
            idxs = [round(i * (len(pngs) - 1) / (n - 1)) if n > 1 else 0 for i in range(n)]
            picks = [(j, pngs[j]) for j in idxs]
        w0, h0 = ffprobe_wh(picks[0][1])
        if not w0:
            die(f"could not read dimensions of {picks[0][1]}")
        cw = args.cell_width
        chh = max(1, round(cw * h0 / w0))
        for idx, p in picks:
            rgba = png_to_rgba(p, cw, chh)
            if rgba is None:
                die(f"failed to decode frame PNG: {p}")
            cells.append((rgba, f"#{idx}"))
        mode = f"dir({len(pngs)} pngs)"
    else:
        dur = ffprobe_duration(src)
        w0, h0 = ffprobe_wh(src)
        if not w0:
            die(f"could not read video dimensions: {src}")
        if not dur or dur <= 0:
            die(f"could not read video duration: {src}")
        cw = args.cell_width
        chh = max(1, round(cw * h0 / w0))
        # Sample inside a window clear of both ends (output-seek is relative to the
        # stream's first frame). 5%..95% of duration keeps clear of EOF rounding.
        lo, hi = 0.05 * dur, 0.95 * dur
        if n == 1:
            rels = [dur * 0.5]
        else:
            rels = [lo + (hi - lo) * i / (n - 1) for i in range(n)]
        for rel in rels:
            rgba = extract_frame_rgba(src, rel, cw, chh)
            if rgba is None:
                # Retry once nudged inward — guards the very last GOP/EOF.
                rgba = extract_frame_rgba(src, min(rel, 0.90 * dur), cw, chh)
            if rgba is None:
                die(f"failed to extract frame at t={rel:.3f}s from {src}")
            cells.append((rgba, f"t={rel:.2f}s"))
        mode = f"video({dur:.2f}s {w0}x{h0})"

    count = len(cells)
    cols = args.cols if args.cols > 0 else max(1, math.ceil(math.sqrt(count)))
    rows = math.ceil(count / cols)
    gap = max(0, args.gap)

    sheet_w = cols * cw + (cols + 1) * gap
    sheet_h = rows * chh + (rows + 1) * gap
    # Sheet background (dark gray, opaque).
    sheet = bytearray(b"\x20\x20\x20\xff" * (sheet_w * sheet_h))

    for k, (rgba, label) in enumerate(cells):
        r = k // cols
        c = k % cols
        ox = gap + c * (cw + gap)
        oy = gap + r * (chh + gap)
        # Blit cell rows into the sheet.
        for y in range(chh):
            dst = ((oy + y) * sheet_w + ox) * 4
            srcoff = y * cw * 4
            sheet[dst:dst + cw * 4] = rgba[srcoff:srcoff + cw * 4]
        if args.label:
            draw_label(sheet, sheet_w, sheet_h, ox + 2, oy + 2, label, scale=2)

    # Resolve output path.
    if args.output:
        out = args.output
    elif os.path.isdir(src):
        out = os.path.join(src, "filmstrip.png")
    else:
        base = os.path.splitext(os.path.basename(src))[0]
        out_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "out")
        os.makedirs(out_dir, exist_ok=True)
        out = os.path.join(out_dir, f"{base}.filmstrip.png")

    os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
    write_png_rgba(out, sheet_w, sheet_h, bytes(sheet))

    sz = os.path.getsize(out)
    print(f"filmstrip: input  = {src}  [{mode}]")
    print(f"filmstrip: frames = {count} in {cols}x{rows} grid, cell {cw}x{chh}")
    print(f"filmstrip: output = {out}")
    print(f"filmstrip: size   = {sheet_w}x{sheet_h} px, {sz} bytes")


if __name__ == "__main__":
    main()
