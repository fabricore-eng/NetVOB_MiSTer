# core/ — FPGA MPEG-2 decoder core (Cyclone V)

A fork of **`mrchrisster/MiSTer_MPEG2`** (which wraps the BSD **`mpeg2fpga`** decoder
by Koen De Vleeschauwer) targeted at the SuperStation One. Decode runs **in fabric**,
independent of the HPS.

> **No RTL imported yet — planning phase.** Findings + injection seam:
> [`../docs/findings.md`](../docs/findings.md). Build/sim/bring-up playbook:
> [`../docs/dev-workflow.md`](../docs/dev-workflow.md).

## Vendoring (planned)
Vendor big deps as **git submodules pinned to a SHA**; keep our edits as **isolated,
re-appliable patch files** (`git apply`) + an idempotent `apply_patches.sh`. The pin
never moves and our diffs stay upstream-offer-able. Vendor:
- `mrchrisster/MiSTer_MPEG2` — the port we build on (`mpg_streamer.sv`, `mem_shim.sv`).
- MiSTer `sys/` framework (board support / `sys_top` / `hps_io`).
- `MiSTer-devel/CDi_MiSTer` — reference for the demux→FIFO→decode→vblank→video-out flow.

## Key facts to carry forward (verified — see findings.md)
- **Input:** decoder ingests video **ES** (`stream_data[7:0]` + `stream_valid`,
  buffered by `vbuf`/`getbits`). The ARM feeds it via the **`sd_*` CD-sector seam**
  (`mpg_streamer.sv`, hardware-verified loading). Pull-paced.
- **Output — drive `VGA_*` (decision):** the decoder's own raster (`r/g/b`, `pixel_en`,
  `h_sync`/`v_sync`, `dot_clk`) → MiSTer `VGA_*` → **ADV7125** 24-bit DAC, for
  **field-exact native 480i**. The **`FB_*` DDR-framebuffer→scaler** path is the
  fallback / HDMI option (easier to get *a* picture, harder to guarantee field-exact
  480i). Follow the CD-i `frameplayer` vblank-latched shape.
- **External RAM:** frame store + circular bitstream buffer via **f2sdram/DDR3**
  (`mem_shim.sv`: 24 MB CMA @ `0x30000000`; SD/480i needs ~4 MB — comfortable).
- **State of upstream:** data path verified, **video output NOT yet confirmed** →
  M1a = "reach confirmed, stable video-out." All clocks from **one PLL**.
- **Target:** native **480i**, 24-bit, **field-exact** (`mpeg2fpga` supports interlaced
  output; field-exactness is the M7 goal). Replace the hardcoded 27 MHz SD clock with a
  validated 480i modeline.

## Build target
Reuse the existing, proven SuperStation build flow from the other project (it already
compiles and runs cores on the SuperStation). Record its Quartus **DEVICE** target /
`sys/` for reproducibility and set ours to match. No porting/board-file blocker; a
DE10-Nano is optional. See [`../docs/dev-workflow.md`](../docs/dev-workflow.md) §0.

## Sim (the #1 de-risk)
`mpeg2fpga` is **Verilog**, CD-i is **SystemVerilog** → **Verilator**. Reuse
`mpeg2fpga`'s `bench/conformance` (MP@ML conformance bitstreams) and **dump a decoded-
frame PNG** to validate decode (incl. interlaced) before building a bitstream. One
writer per trace dir; reproduce before claiming.

## License / upstream
Confirm the exact **BSD** variant of `mpeg2fpga` before redistribution. Coordinate with
upstream (mrchrisster — also authors the VCD creator; Slamy for the CD-i reference).
