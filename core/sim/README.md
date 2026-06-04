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

## Quick start

```sh
cd core/sim
make all        # lint-clean is implied by build; build -> run -> png
# artifact: core/sim/run/tv_out_0001.png
```

Individual rungs: `make lint`, `make build`, `make stream.dat`, `make run`,
`make png`, `make clean`.

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
  352x288 progressive sources. The 480i NTSC target raster for the final
  project will need its own modeline (see "Next steps").

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
   Captured as `core/patches/mpeg2fpga-verilator-bench-fixes.patch` (the
   submodule itself stays at its pinned SHA — re-apply with
   `git -C core/mpeg2fpga apply ../patches/mpeg2fpga-verilator-bench-fixes.patch`).

After these three, lint is **error-free** (only WIDTHEXPAND/WIDTHTRUNC/CASEX
style warnings, expected for loosely-typed 2007 RTL; suppressed in the Makefile).

## Notes / caveats

- **Interlaced output is real.** The `tv_out` PPM header reports
  `# interlaced 1` with PAL field geometry (288 lines/field, halfline 383), and
  the framestore reports `picture_structure frame picture`, `chroma_format
  4:2:0`, `mb_width 45 mb_height 36` (= 720x576). This is the field-cadence
  behaviour the project depends on; verify field handling carefully when porting
  the output raster to NTSC 480i.
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

- `Makefile` — the ladder targets and the load-bearing Verilator flags.
- `prep_stream.sh` — portable `stream.dat` builder (replaces the GNU-only
  `head --bytes` / `xxd -c 1` in the upstream bench Makefile).
- `run_sim.sh` — detached run + frame-count-gated stop.
- `incdir/` — symlinks to the `\`include`-only header files (see blocker #2).
- `run/` — scratch output (gitignored / `make clean`-ed): PPMs, PNG, `run.log`.
