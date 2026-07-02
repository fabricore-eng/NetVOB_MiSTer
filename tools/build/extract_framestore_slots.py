#!/usr/bin/env python3
"""extract_framestore_slots.py — per-slot Y-plane extraction from a sim framestore ppm.

The memshim tb dumps the WHOLE framestore (4 frame slots + OSD, Y+CR+CB each) into one
P3 ppm per frame event. Whole-file md5s are only comparable between runs with IDENTICAL
timing: any timing perturbation (pacing, refresh, staleness) skews the in-progress
content of the OTHER slots at the dump instant, so 'fingerprint differs' does NOT mean
'decode differs'. This tool extracts each slot's settled Y plane so per-frame decode can
be compared honestly across timing-perturbed runs.

Usage: extract_framestore_slots.py <framestore.ppm> [outdir]
Prints one line per slot: slot index, md5 of the raw Y bytes, and the PNG path.
Layout (tb_memshim.v write_framestore): per frame block = Y(height rows) +
CR(height/2) + CB(height/2); frames at block offsets 0..3, OSD last; width x (9*height+26).
"""
import sys, os, re, hashlib

def parse_p3(path):
    with open(path, 'rb') as f:
        data = f.read()
    # strip comments, tokenize
    text = re.sub(rb'#[^\n]*', b'', data)
    toks = text.split()
    assert toks[0] == b'P3', f"not a P3 ppm: {toks[0]!r}"
    w, h, maxv = int(toks[1]), int(toks[2]), int(toks[3])
    vals = toks[4:]
    return w, h, vals

def main():
    ppm = sys.argv[1]
    outdir = sys.argv[2] if len(sys.argv) > 2 else os.path.dirname(ppm) or '.'
    w, total_h, vals = parse_p3(ppm)
    # geometry: total_h = 9*H + pad ; per-frame block = 2*H rows (Y=H, CR+CB=H)
    H = (total_h - 26) // 9 if (total_h - 26) % 9 == 0 else total_h // 9
    block = 2 * H
    os.makedirs(outdir, exist_ok=True)
    for slot in range(4):
        r0 = slot * block
        # Y plane rows r0 .. r0+H-1 ; P3 has 3 values (RGB) per pixel, Y dump is grayscale RGB
        start = r0 * w * 3
        end = (r0 + H) * w * 3
        if end > len(vals):
            print(f"slot{slot}: OUT OF RANGE (ppm truncated?)"); continue
        y = bytes(min(255, max(0, int(vals[i]))) for i in range(start, end, 3))
        md5 = hashlib.md5(y).hexdigest()
        png = os.path.join(outdir, f"slot{slot}_Y.png")
        try:
            from PIL import Image
            Image.frombytes('L', (w, H), y).save(png)
        except ImportError:
            png = png.replace('.png', '.gray')
            open(png, 'wb').write(y)
        print(f"slot{slot}: Y-md5={md5} {png}")

if __name__ == '__main__':
    main()
