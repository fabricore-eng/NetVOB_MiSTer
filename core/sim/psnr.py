#!/usr/bin/env python3
"""psnr.py — Y-plane PSNR of a decoder framestore plane vs an ffmpeg reference.

Replaces eyeballing with a number. Reads:
  - a HW-decoder Y plane (raw 8-bit W*H, from framestore_extract.py's .gray)
  - an ffmpeg software-reference raw gray stream (W*H per frame, display order)

and reports per-reference-frame PSNR(dB). Because the HW framestore is in CODED
(decode) order while ffmpeg raw is in DISPLAY order, and the exact frame index
the HW dump captured can lag, we scan a window of reference frames and report the
BEST match (highest PSNR) plus its index, so a faithful decode is recognized even
if our frame bookkeeping is off by a few.

Usage:
  psnr.py HW.gray REF.gray W H [--scan-from N] [--scan-to M]
PSNR is computed on the 8-bit Y plane. Identical planes => inf (clamped to 99.0).
"""
import argparse
import math
import os
import sys


def load(path, n):
    with open(path, "rb") as f:
        d = f.read()
    if len(d) < n:
        sys.exit(f"psnr: ERROR: {path} has {len(d)} bytes, need {n}")
    return d


def mse(a, b):
    # a, b: equal-length bytes
    s = 0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
    return s / len(a)


def psnr_from_mse(m):
    if m <= 0:
        return 99.0  # identical
    return 10.0 * math.log10((255.0 * 255.0) / m)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("hw")
    ap.add_argument("ref")
    ap.add_argument("w", type=int)
    ap.add_argument("h", type=int)
    ap.add_argument("--scan-from", type=int, default=0)
    ap.add_argument("--scan-to", type=int, default=8)
    args = ap.parse_args()

    fsz = args.w * args.h
    hw = load(args.hw, fsz)[:fsz]
    refsz = os.path.getsize(args.ref)
    nref = refsz // fsz

    best = (-1.0, None, None)  # (psnr, idx, m)
    lo = max(0, args.scan_from)
    hi = min(nref, args.scan_to)
    print(f"psnr: HW={os.path.basename(args.hw)} vs REF={os.path.basename(args.ref)} "
          f"({nref} ref frames, {args.w}x{args.h})")
    with open(args.ref, "rb") as f:
        for idx in range(lo, hi):
            f.seek(idx * fsz)
            ref = f.read(fsz)
            m = mse(hw, ref)
            p = psnr_from_mse(m)
            mark = ""
            if p > best[0]:
                best = (p, idx, m)
                mark = "  <- best"
            print(f"  ref frame {idx:3d}: PSNR = {p:6.2f} dB (MSE {m:8.2f}){mark}")
    print(f"psnr: BEST PSNR = {best[0]:.2f} dB at ref frame {best[1]} (MSE {best[2]:.2f})")
    # Emit a machine-greppable line.
    print(f"PSNR_BEST_DB={best[0]:.2f}")


if __name__ == "__main__":
    main()
