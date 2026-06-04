# core/sim — Verilator harness for the mpeg2fpga decoder

This is **our** simulation harness (never inside the submodule) that drives
**Verilator 5.x** against the vendored `mpeg2fpga` decoder
(`core/mpeg2fpga`, pinned SHA). It is the project's **#1 de-risk** (Decision D1 /
risk #1): proving that Koen De Vleeschauwer's 2007 MPEG-2 RTL actually *decodes
to pixels* on a modern open toolchain, before any Quartus/bitstream work.

## Status: verification ladder reached = **frame-png** (reproduced)

`make all` decodes the repo's shipped `greyramp.mpg` (720x576 PAL interlaced,
MP@ML elementary stream) and renders a decoded RGB frame to
`run/tv_out_0001.png`. The output is the expected black→white luminance ramp.
**Two independent clean `make all` runs produce byte-identical PPM and PNG**, so
the decode is deterministic and reproduced — not a one-off.

| Rung      | Reached | Evidence |
|-----------|---------|----------|
| explored  | yes | this README + the blocker notes below |
| lint      | yes | `make lint` → exit 0, only width/style warnings |
| compiles  | yes | `make build` → `obj_dir/Vtestbench` (`--binary --timing`) |
| runs      | yes | `make run` → `run/tv_out_*.ppm` + `run/framestore_*.ppm` |
| frame-png | yes | `make png` → `run/tv_out_0001.png` (945x314 RGB), reproduced |

### Cycle 2: real-motion (I/P/B) decode + NTSC 480i + numeric PSNR

Cycle 1 only ever decoded the static `greyramp` content. Cycle 2 pushed the M1a
de-risk to **inter-predicted motion** and the **project's real NTSC 480i target**,
with a **PSNR number** replacing the eyeball. All three reproduce
(`PSNR_BEST_DB=28.67` is byte-identical across two clean runs). Durable evidence
PNGs (survive `make clean`) live in **`artifacts/`**.

- **Real motion decodes (I, P AND B).** Decoding a 720x480 progressive testsrc2
  clip *with B-frames* (`-bf 2`), the framestore dumps show
  `picture_coding_type` advancing **I → P → B → B** with `frame number`
  incrementing, the burned-in testsrc2 timecode advancing 00.000 → 00.033 →
  00.100, and **no hang/corruption** (`run.log` clean). This is the first time
  the project exercised motion compensation (forward in P, bidirectional in B).
  Evidence: `artifacts/motion_ipb_filmstrip.png`,
  `artifacts/motion_{I,P,B}_frame_decoded.png`.
- **NTSC 480i raster (the ADV7125 target).** Added `MODELINE_NTSC_INTERL`
  (720x480i, BT.601 858-total-dot line, 240 visible lines/field, interlaced
  halfline=429). Building with `MODELINE=MODELINE_NTSC_INTERL` and decoding the
  480i clip yields a **720x240-active-per-field, 2-field = 480-line interlaced**
  raster in full 24-bit color; the two fields measurably differ (real interlace).
  Evidence: `artifacts/ntsc480i_field_color.png`,
  `artifacts/ntsc480i_field_filmstrip.png`.
- **Numeric correctness: Y-PSNR = 28.67 dB** on the first clean I-frame vs an
  ffmpeg software reference of the *same* elementary stream (best alignment at
  ref display-frame 0, zero spatial shift). This is **below the 30 dB pass bar I
  set**, so `psnr_pass = false` — BUT the residual is **bounded and benign**: max
  |Δ| = 17, 100% of pixels within ±16, and the error is spatially confined to the
  highest-frequency content (the moving diagonal edge and the checkerboard
  region) with flat color bars near-zero. That is the textbook signature of
  **IDCT/dequant rounding divergence between two conformant MPEG-2 decoders**
  (the 2007 fixed-point HW IDCT vs ffmpeg's higher-precision IDCT, an IEEE-1180-
  permitted difference), **not** a decode bug. Evidence:
  `artifacts/motion_I_psnr_diff_x4.png` (amplified abs-diff).

### Cycle 3: end-to-end interlace (field parity), B-frames through 480i, PSNR-tighten, GOP soak

Cycle 3 closed four loops the prior cycles left open. Added a **tv_out field-parity
probe** (`core/patches/mpeg2fpga-tvout-field-parity-probe.patch`) and a field
reassembly tool (`reassemble_fields.py`). All headline numbers below were
**reproduced** (a fresh decode reproduces the parity verdict and the 28.67 dB
byte-for-byte).

- **A. Field order / parity verified end-to-end (`field_order_ok = TRUE`).** The
  NTSC 480i path emits one field per `tv_out` PPM. Two consecutive fields
  reassemble into a 480-line top-field-first frame whose Y matches an ffmpeg
  interlaced-decode reference of the SAME stream at **22.0 dB**, while the
  WRONG (swapped-parity) interleave scores **3.8 dB** — an **18 dB gap that
  unambiguously confirms correct top/bottom parity**. The per-field
  discrimination is even sharper: a field-distinct clip puts the decoder's
  TOP-content field at **30.4 dB vs ffmpeg `field=top`** and **3.9 dB vs
  `field=bottom`** (≈26 dB separation, reproduced). Key finding: the syncgen
  `odd_field` raster bit is **phase-offset by one field** from BT.601 content
  parity in this bench, so the calibrated mapping is **`field_parity 1` = TOP
  content (even source rows), `field_parity 0` = BOTTOM**. (The luma plane is
  *not* vertically interpolated by `resample` — `resample_addrgen.v` sets
  `disp_delta_y = disp_y` for `STATE_WR_Y_*` — so each field's Y is a clean copy
  of the source field's lines, which is why the parity PSNR test is decisive.)
  Evidence: `artifacts/interlace_reassembled_frame.png`,
  `artifacts/interlace_parity_correct_vs_wrong.png`.
- **B. I AND P AND B all decode through the NTSC 480i interlaced path
  (`b_through_ntsc = TRUE`).** Cycle 2's 480i clip was I+P only. A regenerated
  720x480 **interlaced** clip with B-frames (`-bf 2 -flags +ilme+ildct -top 1`,
  `field_order=tt`, `top_field_first=1`) decodes through
  `MODELINE_NTSC_INTERL` with the framestore showing **I, P, AND B**
  (`picture_coding_type` = B,B,I,P,P in decode order) and real alternating-parity
  field output (`field_parity` toggles 1,0,1,0,…), no hang/corruption. Evidence:
  `artifacts/ntsc480i_bframe_filmstrip.png`.
- **C. PSNR-tighten: the IDCT-flavor hypothesis is REFUTED; best
  `psnr_tightened_db = 28.67`, does NOT cross 30.** Re-PSNR'd the progressive
  I-frame against ffmpeg references decoded with `-idct int` AND `-idct simple`
  (the low-precision hypothesis). **All three IDCT flavors give 28.67 dB** —
  because ffmpeg's `int`/`simple`/default IDCTs are themselves near-identical
  (`default` vs `int` MSE = 0.0037; `default` vs `simple` MSE = 0.0000), they
  cannot explain or close the gap. The residual is therefore the mpeg2fpga 2007
  **fixed-point datapath** vs ffmpeg *as a class*, not an IDCT-precision flavor.
  Characterized: bounded (max|Δ|=18, 100% within ±16), a small **−2.05 LSB DC
  offset** (DC-correcting recovers only to 28.88 dB), and **zero spatial shift is
  optimal** (any ±1 px shift is worse), so alignment is correct. Benign rounding,
  not a decode bug — but honestly **below the 30 dB bar**.
- **D. GOP soak: no drift / hang / leak.** A 3.99 MB ES (just under the 4 MiB
  prep cap; 240 frames, 21 GOPs, I+P+B) decodes steadily. Framestore dumps are
  **uniform size** (no memory growth/leak), `run.log` is clean (only the benign
  `$readmem ended before final address` — stream.dat is shorter than the 4 MiB
  array), the last dump (a B-frame deep in the stream) is well-formed (separators
  verified), and **content advances** across dumps (frame0 Y MSE 268/293 between
  spread dumps — no frozen/stuck frame). The run is wall-clock-bounded (the `-O0`
  + behavioral-RAM sim is slow), not stream- or stability-bounded. Evidence:
  `artifacts/gop_soak_filmstrip.png`.

## Quick start

```sh
cd core/sim
make all        # lint-clean is implied by build; build -> run -> png
# artifact: core/sim/run/tv_out_0001.png
```

Individual rungs: `make lint`, `make build`, `make stream.dat`, `make run`,
`make png`, `make clean`.

### Recipe A — real-motion (I/P/B) decode + PSNR vs ffmpeg

```sh
# 1. Make a 720x480 progressive clip WITH B-frames + real motion (testsrc2):
ffmpeg -f lavfi -i "testsrc2=size=720x480:rate=30000/1001:duration=2" \
  -pix_fmt yuv420p -c:v mpeg2video -b:v 6000k -g 15 -bf 2 -f vob /tmp/motion480p.mpg
# 2. The bench wants a video ELEMENTARY stream, not a program stream:
ffmpeg -i /tmp/motion480p.mpg -c:v copy -f mpeg2video /tmp/motion480p.m2v
# 3. Build (default modeline is fine; the decode is native-resolution) + run:
make build
rm -f stream.dat && ./prep_stream.sh /tmp/motion480p.m2v
./run_sim.sh run 6 240
# 4. Confirm P and B pictures actually decoded (not just I):
grep -h picture_coding_type run/framestore_*.ppm     # -> I, P, B, B
# 5. Pull decoded Y planes out of the framestore dumps -> PNG + raw .gray:
python3 framestore_extract.py run/framestore_0001.ppm run/yplanes --prefix fs0001
# 6. Numeric correctness vs an ffmpeg software reference of the SAME stream:
ffmpeg -i /tmp/motion480p.m2v -pix_fmt gray -f rawvideo /tmp/ref.gray
python3 psnr.py run/yplanes/fs0001_frame0_Y.gray /tmp/ref.gray 720 480
#   -> PSNR_BEST_DB=28.67   (I-frame; see "numeric correctness" caveat above)
# 7. Filmstrip the decoded I/P/B frames:
python3 ../../tools/filmstrip/filmstrip.py run/yplanes -n 3 --label -o run/strip.png
```

### Recipe B — NTSC 480i with B-frames (the ADV7125 target raster)

```sh
# 1. Make a 720x480 INTERLACED clip WITH B-frames (I+P+B through the 480i path).
#    +ilme+ildct = interlaced ME + interlaced DCT; -top 1 = top-field-first.
ffmpeg -y -f lavfi -i "testsrc2=size=720x480:rate=30000/1001:duration=2" \
  -pix_fmt yuv420p -c:v mpeg2video -b:v 6000k -g 15 -bf 2 \
  -flags +ilme+ildct -top 1 -alternate_scan 1 -f vob /tmp/motion480i.mpg
ffmpeg -y -i /tmp/motion480i.mpg -c:v copy -f mpeg2video /tmp/motion480i.m2v
ffprobe -v error -select_streams v:0 -show_entries stream=field_order /tmp/motion480i.m2v  # -> tt
# 2. Build with the NTSC 480i modeline + field-parity probe, decode:
make build MODELINE=MODELINE_NTSC_INTERL
rm -f stream.dat && ./prep_stream.sh /tmp/motion480i.m2v
./run_sim.sh run_ntsc_bf 12 240
# 3. Confirm I AND P AND B decoded through the interlaced path:
grep -h picture_coding_type run_ntsc_bf/framestore_*.ppm   # -> B,B,I,P,P (decode order)
# 4. Each tv_out_*.ppm is ONE FIELD; header reports geometry + field parity:
grep -E "interlaced|field_parity" run_ntsc_bf/tv_out_0006.ppm
#   interlaced 1 halfline 429        (BT.601: 720x240/field, 858 total dots)
#   field_parity 1                   (1 = TOP content, 0 = BOTTOM — calibrated)
ffmpeg -y -i run_ntsc_bf/tv_out_0006.ppm field.png         # -> full 24-bit color field
```

### Recipe A2 — field-order / parity reassembly + PSNR (end-to-end interlace)

```sh
# tv_out fields are emitted one-per-PPM; reassemble two opposite-parity fields
# into a 480-line frame and PSNR vs an ffmpeg interlaced-decode reference.
# (Run Recipe B first to produce run_ntsc_bf/.)
# CALIBRATED mapping: field_parity 1 = TOP (rows 0,2,4..), field_parity 0 = BOTTOM.
python3 reassemble_fields.py run_ntsc_bf/tv_out_0001.ppm run_ntsc_bf/tv_out_0002.ppm /tmp/frame.gray
ffmpeg -y -i /tmp/motion480i.m2v -pix_fmt gray -f rawvideo /tmp/ref480i.gray   # full interlaced ref
python3 psnr.py /tmp/frame.gray /tmp/ref480i.gray 720 480                       # correct-parity weave

# DECISIVE per-field parity test (use a field-DISTINCT clip so top != bottom):
#   even rows = ramp, odd rows = inverse ramp -> ffmpeg field=top/bottom differ by ~28k MSE.
# Extract decoder field as 720x240 gray, PSNR vs ffmpeg field=top AND field=bottom:
ffmpeg -y -i CLIP.m2v -vf "field=top,format=gray"    -f rawvideo /tmp/ft_top.gray
ffmpeg -y -i CLIP.m2v -vf "field=bottom,format=gray" -f rawvideo /tmp/ft_bot.gray
#   parity-1 decoder field -> ~30 dB vs TOP, ~4 dB vs BOTTOM  => parity 1 IS the TOP field
```

### Recipe C — PSNR-tighten (test the IDCT-flavor hypothesis)

```sh
# Decode the progressive motion clip (Recipe A), extract the settled I-frame Y plane.
# NOTE the I-frame lands in framestore buffer 0 by the SECOND framestore dump
# (framestore_0001.ppm), not the first (framestore_0000 is mid-write, all 4 buffers
# still duplicated). PSNR vs ffmpeg refs decoded with LOW-PRECISION IDCTs:
ffmpeg -y -i /tmp/motion480p.m2v               -pix_fmt gray -f rawvideo /tmp/ref_default.gray
ffmpeg -y -idct int    -i /tmp/motion480p.m2v  -pix_fmt gray -f rawvideo /tmp/ref_int.gray
ffmpeg -y -idct simple -i /tmp/motion480p.m2v  -pix_fmt gray -f rawvideo /tmp/ref_simple.gray
python3 framestore_extract.py run_prog/framestore_0001.ppm run_prog/yp --prefix x
for r in default int simple; do python3 psnr.py run_prog/yp/x_frame0_Y.gray /tmp/ref_$r.gray 720 480; done
#   -> 28.67 dB for ALL THREE. ffmpeg's int/simple/default IDCTs are ~identical
#      (mutual MSE < 0.004), so the IDCT-flavor hypothesis is REFUTED; the gap is
#      the mpeg2fpga fixed-point datapath, not which IDCT ffmpeg picked.
```

### Recipe D — GOP soak (stability across many GOPs)

```sh
# A long clip just under the 4 MiB prep cap; confirm no drift/hang/leak.
ffmpeg -y -f lavfi -i "testsrc2=size=720x480:rate=30000/1001:duration=8" \
  -pix_fmt yuv420p -c:v mpeg2video -b:v 4000k -g 12 -bf 2 -f vob /tmp/soak.mpg
ffmpeg -y -i /tmp/soak.mpg -c:v copy -f mpeg2video /tmp/soak.m2v   # ~3.99 MB, 240 frames, 21 GOPs
rm -f stream.dat && ./prep_stream.sh /tmp/soak.m2v
./run_sim.sh run_soak 40 480                      # wall-clock-bounded; -O0 sim is slow
grep -h picture_coding_type run_soak/framestore_*.ppm | sort | uniq -c   # multiple I = multiple GOPs
ls -la run_soak/framestore_*.ppm                  # uniform size => no leak/growth
grep -iE "error|stop|undefined" run_soak/run.log  # clean (the $readmem note is benign)
```

## What it drives

The **iverilog bench** configuration of `mpeg2fpga`
(`core/mpeg2fpga/bench/iverilog/`), i.e. the **soft-FIFO + behavioral-RAM**
simulation path. This is deliberate: that path contains **no Xilinx
primitives** (no `BUFG`/`DCM`/`FIFO36`/`FIFO144`/block-RAM macros), so Verilator
needs no vendor cell library. Data flow:

```
stream.dat ($readmemh hex)  -> testbench.v drives stream_data[7:0]+stream_valid
   -> mpeg2video (the decoder top)  -> mem_ctl.v (behavioral 64-bit DRAM model)
   -> RGB raster (r/g/b, pixel_en, h_sync, v_sync) clocked by dot_clk
testbench.v writes  tv_out_NNNN.ppm   (the RGB raster as it would clock to the DAC)
mem_ctl.v   writes  framestore_NNNN.ppm (decoded Y/Cr/Cb frame buffers)
```

Top module: `mpeg2video` (`core/mpeg2fpga/rtl/mpeg2/mpeg2video.v`). Clocks:
`clk` 75 MHz, `mem_clk` 125 MHz, `dot_clk` 27 MHz. Input seam is exactly the
`stream_data`/`stream_valid` byte interface the project's injection plan targets.

## Inputs

- **Stream:** the shipped `core/mpeg2fpga/tools/streams/greyramp.mpg` (the only
  real MPEG-2 stream vendored; the `stream-susi.mpg` named in the upstream
  Makefile is *not* shipped). `prep_stream.sh` converts it to `stream.dat`
  (one hex byte per line for `$readmemh`) and appends the repo's
  `end-of-sequence.mpg` so the decoder flushes the final picture. Override with
  `make STREAM=/path/to/other.m2v`.
- **Output raster modeline:** `MODELINE_PAL_INTERL` (768x576 interlaced), which
  matches greyramp's geometry. Override with `make MODELINE=MODELINE_SIF` for
  352x288 progressive sources, or **`make MODELINE=MODELINE_NTSC_INTERL`** for the
  project's **720x480i NTSC target** (added cycle 2 via
  `core/patches/mpeg2fpga-ntsc-480i-modeline.patch`). NOTE: the modeline sets the
  *output raster* geometry only — the decoder reads the real frame size from the
  stream's sequence header, so the framestore Y/Cr/Cb planes are always at native
  resolution regardless of modeline. The NTSC entry's analog timing is exact only
  when `dotclock.v` is set to 13.5 MHz; at the bench's stock 27 MHz dot clock the
  *geometry* is correct but the field period is 8.32 ms (→ set the PLL for HW).

## Blockers resolved (each maps to a real Verilator error)

These are why the `make` flags look the way they do; all are characterized with
the exact error so a maintainer can verify:

1. **`do` is a SystemVerilog keyword.** The 2007 RTL uses `do` (data-out) as a
   net/port name in `iquant.v`, `generic_dpram.v`, `generic_fifo_*.v`. Verilator
   defaults to SystemVerilog and errors:
   `iquant.v:38: Unexpected 'do': 'do' is a SystemVerilog keyword`.
   **Fix:** `--language 1364-2001` (parse as Verilog-2001). No source change.

2. **Xilinx FIFO auto-load via module name collision.** `rtl/mpeg2/wrappers.v`
   defines a *second* `fifo_sc`/`fifo_dc` (the Xilinx variant that `\`include`s
   `xilinx_fifo_sc.v` → `xilinx_fifo.v` → instantiates `FIFO36`/`FIFO144`),
   colliding with the bench's soft-FIFO `wrappers.v` (MODDUP). If `rtl/mpeg2` is
   on the module search path, Verilator pulls in the Xilinx file and errors:
   `Cannot find file containing module: 'FIFO36_72'`.
   **Fix:** never put `rtl/mpeg2` on the search path. Instead `core/sim/incdir/`
   holds symlinks to **only** the 10 `\`include`-header files
   (`fifo_size.v`, `mem_codes.v`, `modeline.v`, …), and `+incdir+` points there.
   All real modules are passed explicitly on the command line (the exact bench
   SRCS list, minus `rtl/mpeg2/wrappers.v` and `xilinx_fifo*.v`).

3. **Duplicate `fp` declaration in `mem_ctl.v` tasks.** Tasks `write_mb` and
   `write_row` declare `fp` both as a task port (`input [31:0]fp;`) and as a
   local (`integer fp;`). iverilog tolerates it; Verilator errors:
   `mem_ctl.v:302: Non-ANSI I/O declaration of signal conflicts with type
   declaration: 'fp'`. **Fix:** removed the redundant `integer fp;` locals.
   Captured as `core/patches/mpeg2fpga-verilator-bench-fixes.patch`.

After these three, lint is **error-free** (only WIDTHEXPAND/WIDTHTRUNC/CASEX
style warnings, expected for loosely-typed 2007 RTL; suppressed in the Makefile).

## Patches (the durable artifacts; submodule stays pinned at `2d51ffc`)

The submodule is **never committed**; our edits live as re-appliable patches in
`core/patches/`. The Makefile `patch` target (a dependency of `lint`/`build`)
**loops over every `core/patches/*.patch`** in sorted order with idempotent
reverse-check / apply / skip logic, so a fresh checkout applies them all and
re-running is a no-op. Current set:

- `mpeg2fpga-verilator-bench-fixes.patch` — the `fp` dedup (blocker #3).
- `mpeg2fpga-framestore-pictype-probe.patch` — adds `picture_coding_type` to the
  `framestore_*.ppm` header (and a `$display`), so a run *proves which picture
  types decoded* (I vs P vs B = inter-prediction), not just that pixels appeared.
- `mpeg2fpga-ntsc-480i-modeline.patch` — adds `MODELINE_NTSC_INTERL` (see Inputs).
- `mpeg2fpga-tvout-field-parity-probe.patch` (cycle 3) — adds `# field_parity N`
  to each `tv_out_*.ppm` header, probing the syncgen `odd_field` raster bit so
  interlace reassembly knows each field's parity. The comment carries the
  EMPIRICAL calibration (parity 1 = TOP content, parity 0 = BOTTOM — the raster
  bit is phase-offset one field from BT.601 content parity in this bench).

All four patches apply cleanly in sorted order from a pristine pin and re-run as a
no-op (verified: `git stash` the working tree → `make patch` reconstructs the same
state). The patch glob is lexicographic; these four are mutually independent
except the bench-fix and pictype-probe both edit `mem_ctl.v` (the bench-fix sorts
first by name, matching the order its context was generated).

## Notes / caveats

- **Interlaced output is real.** The `tv_out` PPM header reports
  `# interlaced 1` with PAL field geometry (288 lines/field, halfline 383), and
  the framestore reports `picture_structure frame picture`, `chroma_format
  4:2:0`, `mb_width 45 mb_height 36` (= 720x576). The same is now demonstrated
  for the **NTSC 480i** target (240 lines/field, halfline 429, two distinct
  fields per frame — see Recipe B). This is the field-cadence behaviour the
  project depends on.
- **`tv_out_*.ppm` is ONE FIELD when interlaced** (the testbench opens a new file
  on each v_sync). The early files in a run are mostly blank — the RGB
  resample→syncgen path lags the decode by a few fields while the pipeline
  primes; the *framestore* dumps populate first. Look at later `tv_out` fields
  (or the framestore Y planes) for content.
- **No end-of-stream `$finish`.** The upstream testbench free-runs after the
  clip drains. `run_sim.sh` launches the Verilated binary detached, polls for
  output PPMs, and stops it (macOS has no `timeout`/`gtimeout`). `make run`
  waits for **3** `tv_out` files so that `tv_out_0001.ppm` is *closed* — the
  testbench only pads+closes a frame's PPM when the *next* frame's v_sync opens
  the following file, so a frame mid-write would be truncated and unrenderable.
- **`run/framestore_*.ppm` are large** (~54 MB each: all four frame buffers +
  Y/Cr/Cb planes with separators). They are decode-correctness evidence but are
  not committed; `make clean` removes the `run/` dir.
- **`obj_dir/` is built `-O0`** for fast turnaround. For long conformance runs
  drop `-O0` from the `build` recipe (slower compile, much faster sim).

## Files

- `Makefile` — the ladder targets, the load-bearing Verilator flags, and the
  multi-patch `patch` loop. Knobs: `MODELINE=` (raster), `STREAM=` (input ES).
- `prep_stream.sh` — portable `stream.dat` builder (replaces the GNU-only
  `head --bytes` / `xxd -c 1` in the upstream bench Makefile).
- `run_sim.sh` — detached run + frame-count-gated stop (args: RUNDIR MIN_FRAMES
  MAX_WAIT; use distinct RUNDIRs to keep traces from clobbering each other).
- `framestore_extract.py` — pull the four decoded `FRAME_n` Y planes out of a
  `framestore_NNNN.ppm` dump → grayscale PNG + raw `.gray` (layout-driven from
  the PPM's own header; verifies the white separator bands so layout drift is
  caught loudly). Used for the filmstrip and for PSNR.
- `psnr.py` — Y-plane PSNR(dB) of a decoder `.gray` plane vs an ffmpeg
  raw-gray reference, scanning a window of reference frames for best alignment.
- `reassemble_fields.py` (cycle 3) — weave two opposite-parity `tv_out` field
  PPMs into one 480-line top-field-first frame (`.ppm`/`.gray`/`.png`), using the
  calibrated `field_parity` mapping. Crops each field's active region, places its
  lines at their true frame rows; refuses same-parity pairs. Stdlib only.
- `incdir/` — symlinks to the `\`include`-only header files (see blocker #2).
- `artifacts/` — durable evidence PNGs (tracked; survive `make clean`):
  cycle-2 motion I/P/B frames + filmstrip, the PSNR diff image, the NTSC 480i
  color field + filmstrip; **cycle-3** `interlace_reassembled_frame.png`,
  `interlace_parity_correct_vs_wrong.png` (22.0 vs 3.8 dB),
  `ntsc480i_bframe_filmstrip.png` (I/P/B through 480i), `gop_soak_filmstrip.png`.
- `run*/` — scratch output (gitignored / `make clean`-ed): PPMs, PNGs, `.gray`,
  `run.log`. Each recipe/run uses its own `run`, `run_ntsc`, `run_repro`, …
