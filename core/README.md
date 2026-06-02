# core/ — FPGA MPEG-2 decoder core (Cyclone V)

A fork of **`mrchrisster/MiSTer_MPEG2`** (which wraps the BSD **`mpeg2fpga`** decoder
by Koen De Vleeschauwer) targeted at the SuperStation One.

> **No RTL imported yet — planning phase.** Findings + the injection seam:
> [`../docs/findings.md`](../docs/findings.md).

Key facts to carry forward (verified — see findings.md):
- **Input:** decoder ingests video **ES** (`stream_data[7:0]` + `stream_valid`,
  buffered by `vbuf`/`getbits`). The ARM feeds it via the **`sd_*` CD-sector seam**
  (`mpg_streamer.sv` already does this, hardware-verified).
- **Output:** raster **RGB/YCbCr** bus (`r/g/b`, `pixel_en`, `h_sync`/`v_sync`,
  `dot_clk`) → standard MiSTer `VGA_*` → **ADV7125** 24-bit DAC. Follow the CD-i
  core's vblank-latched output shape.
- **External RAM:** frame store + circular buffer via **f2sdram/DDR3**
  (`mem_shim.sv`: 24 MB CMA @ `0x30000000`; SD/480i needs ~4 MB — comfortable).
- **State of upstream:** data path verified, **video output NOT yet confirmed** →
  M1a is "reach confirmed, stable video-out." All clocks from **one PLL**.
- **Target:** native **480i**, 24-bit, **field-exact** (interlaced output is
  supported by `mpeg2fpga`; field-exactness is the M7 validation goal). Replace the
  hardcoded 27 MHz SD clock with a validated 480i modeline.

License: confirm the exact **BSD** variant of `mpeg2fpga` before redistribution.
Coordinate with upstream (mrchrisster, and Slamy for the CD-i reference).
