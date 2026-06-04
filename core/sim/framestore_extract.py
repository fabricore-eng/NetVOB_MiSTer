#!/usr/bin/env python3
"""framestore_extract.py — pull decoded Y planes out of a framestore_NNNN.ppm dump.

The mpeg2fpga bench (bench/iverilog/mem_ctl.v, task write_framestore) dumps the
ENTIRE decoder framestore as one tall ASCII-P3 PPM each time a frame buffer's
Y-base is written. The layout, top to bottom, is FIVE stacked regions:

    FRAME_0  : Y (W x H)   then CR (W/2 x H/2)  then CB (W/2 x H/2)
    FRAME_1  : Y, CR, CB
    FRAME_2  : Y, CR, CB
    FRAME_3  : Y, CR, CB
    OSD      : (W x H)

Each region is preceded by `2*mb_width` rows of all-255 ("white separator").
Because R==G==B for every plane (it dumps the raw 8-bit sample three times),
each pixel is a single 0..255 value. We only need the four FRAME_n Y planes
(W x H) to (a) eyeball motion via a filmstrip and (b) compute Y-PSNR vs a
software reference.

This parser is layout-driven (it walks the documented region order using the
W/H/mb_width from the PPM's own `#` header comments) rather than guessing, and
it VERIFIES each separator band is actually ~255 so a layout drift is caught
loudly instead of silently shifting the crop.

Usage:
  framestore_extract.py FRAMESTORE.ppm OUTDIR [--prefix P] [--which 0,1,2,3]
    Writes OUTDIR/P_frameN_Y.png (8-bit grayscale) for each requested buffer N,
    plus OUTDIR/P_frameN_Y.gray (raw 8-bit, W*H bytes) for PSNR alignment.

Deps: Python stdlib only (writes PNG by hand; no PIL).
"""
import argparse
import os
import struct
import sys
import zlib


def die(msg, code=1):
    print(f"framestore_extract: ERROR: {msg}", file=sys.stderr)
    sys.exit(code)


def write_png_gray(path, w, h, data):
    """data: bytes length w*h, 8-bit grayscale, row-major top-to-bottom."""
    def chunk(tag, payload):
        out = struct.pack(">I", len(payload)) + tag + payload
        crc = zlib.crc32(tag + payload) & 0xFFFFFFFF
        return out + struct.pack(">I", crc)
    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0)  # color type 0 = grayscale
    raw = bytearray()
    stride = w
    for y in range(h):
        raw.append(0)  # filter byte
        raw += data[y * stride:(y + 1) * stride]
    idat = zlib.compress(bytes(raw), 9)
    with open(path, "wb") as f:
        f.write(sig)
        f.write(chunk(b"IHDR", ihdr))
        f.write(chunk(b"IDAT", idat))
        f.write(chunk(b"IEND", b""))


def header_int(comments, key):
    for line in comments:
        # comments look like: "# horizontal_size   720"
        toks = line.lstrip("#").split()
        if len(toks) >= 2 and toks[0] == key:
            return int(toks[1])
    return None


def main():
    ap = argparse.ArgumentParser(description="Extract decoded Y planes from a framestore PPM.")
    ap.add_argument("ppm", help="framestore_NNNN.ppm")
    ap.add_argument("outdir", help="output directory")
    ap.add_argument("--prefix", default=None, help="output filename prefix (default: ppm basename)")
    ap.add_argument("--which", default="0,1,2,3",
                    help="comma list of frame buffers to extract (default all four)")
    args = ap.parse_args()

    if not os.path.isfile(args.ppm):
        die(f"not found: {args.ppm}")
    os.makedirs(args.outdir, exist_ok=True)
    prefix = args.prefix or os.path.splitext(os.path.basename(args.ppm))[0]
    want = {int(x) for x in args.which.split(",") if x.strip() != ""}

    # --- read header comments + the dimension line, then slurp all numbers. ---
    comments = []
    pix_w = pix_h = None
    pict_type = "?"
    with open(args.ppm, "r") as f:
        first = f.readline().strip()
        if first != "P3":
            die(f"not an ASCII P3 PPM (got {first!r})")
        # Read lines until we hit the "W H MAX" dimension line (no leading '#').
        dim_tokens = None
        for line in f:
            s = line.strip()
            if not s:
                continue
            if s.startswith("#"):
                comments.append(s)
                if "picture_coding_type" in s:
                    pict_type = s.split()[-1]
                continue
            # first non-comment, non-empty line is "W H MAXVAL"
            dim_tokens = s.split()
            break
        if not dim_tokens or len(dim_tokens) < 3:
            die("could not find PPM dimension line")
        sheet_w, sheet_h = int(dim_tokens[0]), int(dim_tokens[1])
        # Remaining tokens in the file are the RGB samples.
        # Read the rest in one go (the file is ASCII but bounded ~45 MB).
        rest = f.read()

    pix_w = header_int(comments, "horizontal_size")
    pix_h = header_int(comments, "vertical_size")
    mb_width = header_int(comments, "mb_width")
    mb_height = header_int(comments, "mb_height")
    if pix_w is None or pix_h is None or mb_width is None or mb_height is None:
        die("PPM header missing horizontal_size/vertical_size/mb_width/mb_height")

    if sheet_w != pix_w:
        die(f"sheet width {sheet_w} != horizontal_size {pix_w} (layout assumption broken)")

    # All RGB samples as a flat int list. R==G==B per pixel; take every 3rd.
    # The dump ends with a trailing "# not truncated" comment line; drop any
    # non-numeric trailing tokens (comments) so the count matches exactly.
    nums = rest.split()
    expected_samples = sheet_w * sheet_h * 3
    if len(nums) > expected_samples:
        # Trim trailing non-numeric tokens (the footer comment) — keep only the
        # leading run of pixel samples.
        nums = nums[:expected_samples]
    if len(nums) != expected_samples:
        die(f"sample count {len(nums)} != expected {expected_samples} "
            f"({sheet_w}x{sheet_h} RGB) — PPM truncated/corrupt")
    # Convert to a row-major grayscale sheet (one value per pixel = R channel).
    # sheet[row][col].
    #
    # Layout arithmetic (derived from bench/iverilog/mem_ctl.v write_mb), in
    # IMAGE ROWS of width sheet_w. Newlines in the P3 file are cosmetic; only the
    # running count of RGB triples determines pixel position.
    #   separator band : write_mb emits 2*mb_width lines * 8px = 2*mb_width*8 px
    #                     = exactly sheet_w px  => SEP = 1 image row.
    #   Y plane (blk=4) : 16*mb_height lines * (2*mb_width*8) px = pix_h rows.
    #   chroma (blk=1)  : 8*mb_height lines, each = mb_width*8 (data) + mb_width*8
    #                     (interleaved white) px = sheet_w px => 8*mb_height rows
    #                     = pix_h/2 rows.
    # Each write_mb call = leading SEP + plane + trailing SEP.
    sep = (2 * mb_width * 8) // sheet_w          # == 1
    y_rows = (16 * mb_height * (2 * mb_width * 8)) // sheet_w   # == pix_h
    cr_rows = (8 * mb_height * (mb_width * 8 + mb_width * 8)) // sheet_w  # == pix_h/2
    if y_rows != pix_h:
        die(f"computed Y rows {y_rows} != vertical_size {pix_h} (layout drift)")

    # One FRAME_n region = write_mb(Y) + write_mb(CR) + write_mb(CB), each with
    # its own leading+trailing separator.
    region_block = (sep + y_rows + sep) + (sep + cr_rows + sep) + (sep + cr_rows + sep)

    def row_value(row, col):
        # index into nums of the R sample of (row,col)
        i = (row * sheet_w + col) * 3
        return int(nums[i])

    def verify_separator(start_row):
        # Sanity: each separator band is exactly 1 image row and should be all
        # 255. Check a spread of columns across that row.
        for r in range(start_row, start_row + max(1, sep)):
            for c in (0, sheet_w // 4, sheet_w // 2, 3 * sheet_w // 4, sheet_w - 1):
                if row_value(r, c) != 255:
                    return False
        return True

    written = []
    for n in sorted(want):
        if n > 3:
            continue
        base = n * region_block
        sep_start = base
        y_start = base + sep
        if y_start + pix_h > sheet_h:
            die(f"frame {n} Y plane runs past sheet height "
                f"({y_start + pix_h} > {sheet_h}) — layout assumption broken")
        if not verify_separator(sep_start):
            print(f"framestore_extract: WARN: separator band before FRAME_{n} Y "
                  f"is not all-255 (layout may have drifted)", file=sys.stderr)
        # Extract the Y plane.
        plane = bytearray(pix_w * pix_h)
        for r in range(pix_h):
            srow = y_start + r
            roff = srow * sheet_w
            doff = r * pix_w
            for c in range(pix_w):
                plane[doff + c] = int(nums[(roff + c) * 3])
        # Skip dead/blank buffers (all-zero or all-same) only for reporting.
        nonzero = any(plane)
        png_path = os.path.join(args.outdir, f"{prefix}_frame{n}_Y.png")
        raw_path = os.path.join(args.outdir, f"{prefix}_frame{n}_Y.gray")
        write_png_gray(png_path, pix_w, pix_h, bytes(plane))
        with open(raw_path, "wb") as g:
            g.write(bytes(plane))
        mean = sum(plane) / len(plane)
        written.append((n, png_path, mean, nonzero))

    print(f"framestore_extract: {args.ppm}")
    print(f"  picture_coding_type = {pict_type}, native = {pix_w}x{pix_h}, "
          f"sheet = {sheet_w}x{sheet_h}, mb_width = {mb_width}")
    for n, p, mean, nz in written:
        print(f"  FRAME_{n} Y -> {p}  (mean={mean:.1f}, nonzero={nz})")


if __name__ == "__main__":
    main()
