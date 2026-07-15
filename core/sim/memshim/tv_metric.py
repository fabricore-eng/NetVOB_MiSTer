#!/usr/bin/env python3
"""tv_metric.py — per-field brightness metric for memshim tv_out_*.ppm dumps.

The scanout-starvation oracle metric (docs/handoffs/dvd.md "Reproduce/validate"):
per tv_out field, mean of the luma (L) histogram; healthy "content" fields reach
~74 on the animated clip, starved fields collapse to <15 (mixer emits Y=16 black).
The LAST ppm is usually partial (sim killed mid-field) and is dropped.

Usage: python3 tv_metric.py RUNDIR [RUNDIR...]
Prints one line per field and a summary: peak / mean-of-content (mean>25) fields.
Pure-stdlib P3 parser (no PIL dependency).
"""
import sys, glob, os


def field_mean(path):
    """Mean gray value over all pixels of a P3 ppm (fast token scan)."""
    with open(path, "rb") as f:
        data = f.read()
    lines = data.split(b"\n")
    if not lines or lines[0].strip() != b"P3":
        return None
    toks = []
    for ln in lines[1:]:
        if ln.startswith(b"#"):
            continue
        toks.extend(ln.split())
    if len(toks) < 3:
        return None
    w, h = int(toks[0]), int(toks[1])
    vals = toks[3:]
    n = (len(vals) // 3) * 3
    if n < 3 or (w * h * 3) > len(vals) * 2:  # allow partial but not tiny
        pass
    total = 0
    cnt = 0
    for i in range(0, n, 3):
        # ITU-601-ish luma; equal weights are fine for a starvation metric,
        # but keep 0.299/0.587/0.114 to match prior Image.convert('L') numbers.
        r, g, b = int(vals[i]), int(vals[i + 1]), int(vals[i + 2])
        total += 299 * r + 587 * g + 114 * b
        cnt += 1
    if cnt == 0:
        return None
    return total / (cnt * 1000.0)


def run(rundir):
    ppms = sorted(glob.glob(os.path.join(rundir, "tv_out_*.ppm")))
    if len(ppms) > 1:
        ppms = ppms[:-1]  # drop the last (partial) field
    means = []
    for p in ppms:
        m = field_mean(p)
        if m is None:
            continue
        means.append((os.path.basename(p), m))
        print(f"  {os.path.basename(p)}  mean={m:.1f}")
    if not means:
        print(f"{rundir}: NO fields")
        return
    vals = [m for _, m in means]
    content = [v for v in vals if v > 25]
    peak = max(vals)
    print(f"{rundir}: fields={len(vals)} peak={peak:.1f} "
          f"content_fields={len(content)} content_mean={sum(content)/len(content):.1f}"
          if content else
          f"{rundir}: fields={len(vals)} peak={peak:.1f} content_fields=0 (STARVED)")


if __name__ == "__main__":
    for d in sys.argv[1:]:
        print(f"== {d} ==")
        run(d)
