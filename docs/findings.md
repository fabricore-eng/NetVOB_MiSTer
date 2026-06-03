# Findings — verified prior art and the FPGA injection seam

Every claim below is tagged **CONFIRMED / PARTIALLY CONFIRMED / CORRECTED /
UNCONFIRMED / REFUTED** and cited. This is the result of investigating the prior
art against the *actual* repositories, as requested. The headline correction is in
§1; the injection seam (deliverable (a)) is in §5.

---

## 1. Headline correction — "decode is the done 80%"

**CORRECTED.** The decode *algorithm* is mature open RTL; a Cyclone V *port* exists
but is **unproven**. Specifically:

- The MPEG-2 decoder **RTL** (`mpeg2fpga`, §2) is real, BSD-licensed, full-pipeline,
  MP@ML — **CONFIRMED**. But it is a **2007 Xilinx Virtex-5** design.
- A **Cyclone V / MiSTer port** (`MiSTer_MPEG2`, §4) exists and is non-trivial; its
  **data path is hardware-verified** — **CONFIRMED**. But a **working decoder with
  confirmed video output is UNCONFIRMED** — by the port's own engineering notes it
  is mid-debug (FSM hangs, `mem_shim` "not yet compiled," "no full video output
  confirmation"), with **no audio, no A/V sync, a hardcoded 27 MHz SD pixel clock,
  no release, and zero independent verification.**

**Implication for the plan:** the remaining decoder work (bring-up → confirmed
video → field-exact 480i → audio → A/V sync) is the project's center of gravity,
not a solved 80%. This drives Milestones M0–M1 and the #1 risk. The *architecture*
is unchanged.

---

## 2. `mpeg2fpga` — the open MPEG-2 decoder RTL — **CONFIRMED**

- **Author / origin:** Koenraad (Koen) De Vleeschauwer, 2007–2009.
- **Repos:** OpenCores `https://opencores.org/projects/mpeg2fpga`; GitHub mirror
  `https://github.com/OldRepoPreservation/mpeg2fpga`; author site
  `http://www.kdvelectronics.eu/mpeg2fpga/mpeg2fpga.html`.
- **License:** **BSD-family — CONFIRMED.** Source headers carry the classic BSD
  "AS IS"/no-liability disclaimer; OpenCores tags it "BSD." *Likely 2-clause
  (PARTIALLY CONFIRMED — the full numbered-clause list was not read verbatim, so
  2- vs 3-clause is not 100% pinned).* **Action: confirm exact BSD variant before
  redistribution.**
- **What it decodes — CONFIRMED:** MPEG-2 video, **4:2:0, Main Profile @ Main Level
  (MP@ML)**. User guide: decodes MPEG-2 4:2:0 program streams. Default build targets
  352×288 (SIF) with **4 MB** external RAM; defining `MP_AT_HL` gives an alternate
  mapping up to **1920×1088 (HDTV)** with **16 MB** RAM.
- **Interlaced support — CONFIRMED (important for 480i):** the user guide states an
  `interlaced` option: *"If interlaced is asserted, vertical sync is delayed
  one-half scan line at the end of odd fields."* So **interlaced raster output is
  supported.** (Whether the full path is *field-exact* on arbitrary DVD content is a
  validation item — see risk register.)
- **Full pipeline in fabric — CONFIRMED.** `rtl/mpeg2/` contains discrete hardware
  stages: VLD (`vld.v`, `vld_codes.v`, `vlc_tables.v`), inverse-quant + run-length
  (`iquant.v`, `rld.v`), IDCT (`idct.v`), motion compensation (`motcomp.v` +
  `motcomp_addrgen.v`, `motcomp_recon.v`, `motcomp_motvec.v`, `motcomp_picbuf.v`,
  `motcomp_dcttype.v`, `motcomp_dctcodes.v`), frame store (`framestore.v`,
  `framestore_request.v`, `framestore_response.v`), chroma resample (`resample*.v`),
  output (`pixel_queue.v`, `mixer.v`, `osd.v`, `yuv2rgb.v`, `syncgen.v`,
  `modeline.v`).

### 2.1 Bitstream INPUT path — CONFIRMED

- Top module: **`mpeg2video.v`** (module `mpeg2video`).
- Compressed-data ingress: **`stream_data` [7:0]** (8-bit, byte-aligned,
  synchronous with `clk`) + **`stream_valid`**. That is the *entire* compressed
  ingress — a simple valid-qualified byte bus (not Ethernet at the core level).
- Buffering: stream is spooled through a **circular video buffer in external RAM**;
  on-chip FIFOs **`vbuf_write_fifo` / `vbuf_read_fifo`** (`vbuf.v`), then bit
  extraction via **`getbits` / `getbits_fifo`** (`getbits.v`).
- *Nuance:* the top-module port comment calls `stream_data` a "packetized elementary
  stream input" while the user guide says "program streams." In practice it ingests
  an MPEG-2 video ES as a byte stream. → **The ARM should hand the decoder a clean
  video ES** (we demux PS → ES on the ARM; see [`transport.md`](transport.md)).

### 2.2 Video OUTPUT path — CONFIRMED

- Outputs **both** formats: RGB **`r`,`g`,`b`** (8-bit each) and YCbCr **`y`,`u`,`v`**.
- Qualifiers/timing: **`pixel_en`**, **`h_sync`**, **`v_sync`**, **`c_sync`**,
  clocked by **`dot_clk`** (the pixel clock, "typically 25–75 MHz"; ~38.21 MHz on
  the reference board). Raster timing from `syncgen.v`/`modeline.v`; RGB conversion
  in `yuv2rgb.v`. **No packetized output — a raster pixel bus**, which is exactly
  what a MiSTer video-out / the ADV7125 wants.

### 2.3 External RAM dependency — CONFIRMED

- Frame stores + compressed circular buffer live off-chip via a request/response
  interface on the top module: **`mem_req_rd_cmd/_addr/_dta/_en/_valid`** and
  **`mem_res_wr_dta/_en/_almost_full`**, on a dedicated **`mem_clk`** (typ.
  133–166 MHz). Sizes: **4 MB** SD / **16 MB** HD. On the SuperStation this maps to
  the shared DDR3 via the f2sdram bridge (the `MiSTer_MPEG2` port does exactly this).
- **Reference platform:** Xilinx **ML505 / Virtex-5 XC5VLX50T**, decoder ~50% of the
  FPGA, 75 MHz decoder clock. → **Cyclone V resource/timing fit is a porting risk**
  (BlockRAM shapes, multipliers, DDR latency differ from Virtex-5).
- **Conformance bench — CONFIRMED (a de-risk lever):** `bench/conformance` ("`make
  clean test` simulates all MP@ML conformance test bitstreams"). It's Verilog → drive
  it under **Verilator** and dump decoded frames to **PNG** to validate decode (incl.
  interlaced) *before* building a bitstream. See [`dev-workflow.md`](dev-workflow.md) §5.

---

## 3. MiSTer CD-i core + "DVC" (MPEG-1 FMV) — **CONFIRMED as a flow template, not the engine**

- **Repos:** canonical `https://github.com/MiSTer-devel/CDi_MiSTer`; active upstream
  `https://github.com/Slamy/CDi_MiSTer`. Maintainer handle **"Slamy"** (real name
  not public). Credits CD-i Fan, MooglyGuy; uses TG68K.C, opencores 68hc05.
- **"DVC branch" — REFUTED as a branch / CORRECTED:** no git branch named
  DVC/MPEG/FMV exists. The Digital Video Cartridge / VMPEG code is the **`rtl/mpeg/`
  subtree on `main`** (`fmv/` video + `fma/` audio), shipped behind an OSD toggle
  "Disable VMPEG DVC," marked experimental.
- **It is MPEG-1, progressive SIF, and a *hybrid* decoder — CONFIRMED:** decode is
  orchestrated by a **`VexiiRiscv` RISC-V softcore running firmware** (`firmware.mem`)
  plus HW blocks (`dct_coeff_huffman_decoder.sv`, 3× `macroblock_worker.sv`). →
  **Great reference for flow; not the MPEG-2 interlaced engine the goal needs.** DVD
  VOBs are interlaced MPEG-2; an MPEG-1 progressive decoder fundamentally cannot
  produce field-exact 480i.
- **`mister_cdi_vcd_creator` produces MPEG-1 VCD — CONFIRMED** (despite "MPEG-2"
  wording): `ffmpeg → mpeg2enc -f 1 → mplex -f 1 → vcdimager`, output `.m1v`,
  352×288/240, 1150 kbps CBR, GOP 12/15, MP2 44.1 kHz audio. Useful **mux-side
  reference**, but for MPEG-1.

### 3.1 CD-i decode → video-out path (verbatim, the template)

```
disc sectors (rtl/hps_cd_sector_cache.sv)
   └─► SCC68070 bus / DMA  ──►  rtl/vmpeg.sv (module vmpeg)
        compressed bytes enter via:  dma_data_valid = ack && dtc      (DMA)
                                     mpeg_xferwrite  = addr[15:1]==15'h206F && ...  (CPU reg 0x206F)
                                     → mpeg_data[7:0]
   └─► rtl/mpeg/demuxer.sv (module mpeg_demuxer)   PS demux: 00 00 01 start codes,
        PACK/PES, SCR/DTS, audio 0xC? vs video 0xE?   ("ISO 11172" not in source — inference)
   └─► mpeg_video.sv:  data_byte/data_strobe → mpeg_input_stream_fifo_32k
        (DUAL-CLOCK CDC FIFO: clkw = clk30  →  clkr = clk_mpeg)
   └─► VexiiRiscv + start_code_decoder + dct_coeff_huffman_decoder + macroblock_worker ×3
        → YUV frames into DDR (arbitrated by ddr_mux4)
   └─► yuv_frame_adr_fifo.sv (instance "readyframes"): q = for_display  (frame ADDRESSES)
   └─► frameplayer.sv: reads ddr_luma/chroma_line_buffer, BT.601 YCbCr→RGB:
        r = ((Y-16)*298 + 409*(Cr-128))/256
        g = ((Y-16)*298 - 100*(Cb-128) - 208*(Cr-128))/256
        b = ((Y-16)*298 + 516*(Cb-128))/256
        latched on vblank: fetch_and_show_frame <= latched_frame_valid && show_on_next_video_frame
        → rgb888_s vidout (= fmv_video_out)
   └─► cditop.sv plane mux:
        vidout = (mcd212_vsd || force_dvc_video) ? fmv_video_out : mcd212_video_out
   └─► CDi.sv (emu): {r,g,b}=cdi_video_out; VGA_R/G/B=r/g/b; VGA_HS=HSync; VGA_VS=VSync;
        VGA_DE = ~(HBlank|VBlank); CE_PIXEL = ce_pix; CLK_VIDEO = clk_sys   (no scandoubler in-path)
```

**Why this matters:** it is a complete, working demux→FIFO→decode→vblank-latched
RGB→plane-mux→MiSTer-video-out reference inside the MiSTer framework. Our decoder's
output stage should follow this shape (vblank-latched RGB raster → standard `VGA_*`
→ ADV7125). Source files:
`https://github.com/MiSTer-devel/CDi_MiSTer/blob/main/rtl/{vmpeg.sv,cditop.sv,mcd212.sv,mpeg/demuxer.sv,mpeg/fmv/mpeg_video.sv,mpeg/fmv/frameplayer.sv,mpeg/fmv/yuv_frame_adr_fifo.sv}`.

---

## 4. `mrchrisster/MiSTer_MPEG2` — the Cyclone V port we fork — **PARTIALLY CONFIRMED**

- **Repo:** `https://github.com/mrchrisster/MiSTer_MPEG2` (created Feb 2026; 3 stars,
  **no release, no `.rbf`**). Wraps `mpeg2fpga` for Cyclone V (DDR3 / Avalon-MM /
  f2sdram). A genuine, substantial effort — **not vaporware**, but **not finished**.
- **Streaming front-end — CONFIRMED & hardware-verified:** **`mpg_streamer.sv`**
  feeds `.mpg` data via the MiSTer **`sd_*` block interface** (`img_mounted`,
  `sd_lba`, `sd_rd`, `sd_ack`), **deliberately bypassing the slow `ioctl` bus**
  ("Pushing gigabytes via `ioctl`… completely chok[es] the decoder's required
  bandwidth"). "Mirrors the CDi/MegaCD" pattern. **Hardware-verified (2026-02-21):**
  loads test files (5500 sectors = 2.8 MB) into the decoder's FIFO. → **This is the
  injection seam, already proven on real hardware.**
- **DDR3 mapping — CONFIRMED (with scars):** **`mem_shim.sv`** maps the decoder's
  external-RAM interface onto Cyclone V DDR3 via f2sdram. Address formula
  `ram_address <= {7'b0011000, addr}` = 8-byte stride inside the **24 MB CMA at
  `0x30000000`**, fitting a 15.5 MB HD frame buffer. (A wrong stride hit TrustZone
  protection → permanent `waitrequest=1` hangs — illustrative of the porting
  hazards.) FSM: `S_IDLE`/`S_WAIT`, 1-entry skid; `mem_res_wr_en <= ddr3_readdatavalid`
  every cycle to avoid deadlock.
- **Current state — UNCONFIRMED working:** streamer verified, **full video pipeline
  not confirmed on hardware** ("no full video output confirmation"; references a
  past "prior working config that produced video"; `mem_shim` "Rewritten… Awaiting
  recompile and hardware test… Not yet compiled"). Clocks: `dot_clk = clk_sys =
  27 MHz`, `CE_PIXEL = 1'b1`; all clocks must derive from one PLL.
- **Known limitations (verbatim) — CONFIRMED:** *Framerate* — "VSYNC-paces itself"
  (a 25 fps PAL file "will execute in 'fast forward'" on NTSC); *HD modeline
  switching* — "currently drives a hardcoded 27 MHz pixel clock intended for SD";
  *Audio* — "handles only video bitstreams." Recommends testing with "NTSC 480i/p
  30/60 FPS encoded `.mpg` files." → **480i is an explicit intended target.**
- Files: `rtl/mpg_streamer.sv`, `rtl/mem_shim.sv`, `emu.sv`, `modeline.v`; docs
  `doc/MiSTer_MPEG2_Porting_Notes.md`, `doc/MiSTer_DDR3_Memory_Mapping.md`,
  `doc/BUG_FIX_LOG.md`.

**`mrchrisster` is also the author of `mister_cdi_vcd_creator`** → a key potential
**upstream collaborator** for this project.

---

## 5. The injection seam (deliverable (a)) — **CONFIRMED, ranked**

The exact seam where a live stream replaces/augments the disc-image source. Verified
against `Template_MiSTer/sys/hps_io.sv` and `Main_MiSTer/{user_io.cpp, fpga_io.cpp,
scaler.cpp}`.

### Seam #1 (RECOMMENDED) — the `sd_*` CD-sector handshake

- **RTL signals (`hps_io.sv`):** `img_mounted`, `img_size[63:0]`, `sd_lba[VDNUM]`,
  `sd_blk_cnt[6]`, `sd_rd`, `sd_wr`, `sd_ack`, `sd_buff_addr`, `sd_buff_dout`,
  `sd_buff_din[VDNUM]`, `sd_buff_wr`.
- **Handshake (read = HPS→core, the injection direction):** core asserts `sd_rd[n]`
  with `sd_lba[n]`; HPS polls (`UIO_GET_SDSTAT`), reads the sector, pushes it via
  `UIO_SECTOR_RD | ack` → `sd_buff_dout`/`sd_buff_addr`/`sd_buff_wr`; completes with
  `sd_ack[n]`. HPS caches 16 sectors/disk.
- **Where to splice:** `user_io.cpp` has format-specific handlers incl.
  **`cdi_read_cd`** and `psx_read_cd` — i.e. CD cores (including CD-i) already pull
  sectors through the HPS. **Replace the HPS-side image read (`FileSeek`/
  `FileReadAdv`) with a read from the network ring buffer.** Minimal RTL change;
  built-in pacing (core pulls only as the decoder drains). **`MiSTer_MPEG2`'s
  `mpg_streamer.sv` already does exactly this and is hardware-verified.**
- **Bandwidth:** the `sd_*`/SPI transport (`fpga_spi`, bit-banged GPIO,
  `SSPI_STROBE (1<<17)`) tops out ~tens of MB/s — but a DVD-rate MPEG-2 stream is
  ~6–15 Mbps ≈ **<2 MB/s**, so there's ample headroom. **Bandwidth is not the
  constraint for compressed video.**

### Seam #2 — shared DDR3 ring via the f2sdram bridge (highest throughput)

- Core-facing `DDRAM_*` Avalon port (`sys_top.v`): `DDRAM_CLK`, `DDRAM_ADDR[28:0]`,
  `DDRAM_BURSTCNT[7:0]`, `DDRAM_BUSY`, `DDRAM_DOUT[63:0]`, `DDRAM_DOUT_READY`,
  `DDRAM_RD`, `DDRAM_DIN[63:0]`, `DDRAM_BE[7:0]`, `DDRAM_WE`. HPS `mmap`s the shared
  DDR3 (`fpga_io.cpp` `shmem_map`, `do_bridge` brings up h2f/f2h).
- **PARTIALLY CONFIRMED:** the plumbing is bidirectional and proven for FPGA→HPS
  (framebuffer screenshots via `scaler.cpp` `mister_scaler_read`); **HPS→FPGA for
  *input* data has no upstream precedent** — it's a design to build. Overkill for
  compressed bitrate; keep as an option if buffering/latency demands it.

### Seam #3 — `ioctl` continuous pipe (simplest prototype)

- `ioctl_download`/`ioctl_wr`/`ioctl_dout[DW:0]`/`ioctl_addr[26:0]` with
  **`ioctl_wait` as built-in backpressure**. Works as a continuous pipe if you hold
  `ioctl_download` and keep streaming `FIO_FILE_TX_DAT`. SPI-bandwidth-limited and
  not an existing runtime-streaming idiom — **UNCONFIRMED as a standing pattern**,
  but fine at compressed bitrates for a quick bring-up.

**Decision:** standardize on **Seam #1** (matches `MiSTer_MPEG2` and the CD-i core;
lowest RTL risk). Keep Seam #2 as the escape hatch for latency/buffering.

### Output integration (the other seam — decoded video → DAC)
Two ways to get decoded frames to the ADV7125:
- **Raster `VGA_*` (RECOMMENDED):** the decoder already produces a pixel raster
  (`mpeg2fpga` `syncgen`; CD-i `frameplayer`) → MiSTer `VGA_R/G/B` + `VGA_HS/VS/DE` +
  `CE_PIXEL`/`CLK_VIDEO` → `sys/` routes to the analog DAC. **Best for field-exact
  native 480i** (you own the interlaced timing/parity into the DAC).
- **`FB_*` DDR framebuffer (fallback/HDMI):** write frames to DDR3 and hand them to the
  MiSTer scaler (`FB_EN`/`FB_BASE`/`FB_FORMAT`/`FB_WIDTH/HEIGHT`). Easiest first
  picture, but the scaler scandoubles/scales — harder to guarantee field-exact 480i.

Detail + decision rationale: [`dev-workflow.md`](dev-workflow.md) §3.1.

---

## 6. Hardware facts — **CONFIRMED** (see PLAN §3 for the short version)

- **SuperStation One:** Cyclone V SX `5CSXFC6D6F31I7N` *(part per tech-press →
  PARTIALLY CONFIRMED; the user's working build already compiles/runs cores on the
  SuperStation, and that build's Quartus DEVICE target is authoritative for our
  compile)*, dual A9 @ 800 MHz, ~110K LE, **128 MB SDRAM**, stock MiSTer, **ADV7125**
  triple-8-bit (24-bit) analog DAC, built-in NFC/Zaparoo, Wi-Fi/BT, dual PS1 SNAC.
  Retro Remake / Taki Udon. *"480i" is set by the core's video timing, not the DAC.*
- **SuperDock:** DVD-RW (tray), NVMe M.2 2280 (unpopulated), USB-C "PC Mode" — all
  CONFIRMED. Disc loading is per-system "firmware work" (PS1, Sega CD, Saturn
  preliminary). **Optical-drive-as-OS-block-device: UNDOCUMENTED (parked).**
- **Raspberry Pi 5 (BCM2712):** **HEVC 4Kp60 decode only**; **no** HW H.264 decode
  (block removed); **no** HW encode of any kind; **no** MPEG-2 hardware. → MPEG-2
  encode is **software** (`ffmpeg mpeg2video`). One SD stream is comfortable on the
  A76 *alone*; **concurrent Plex transcoding contends for the same cores** (no
  encoder to offload to). License-key docs confirm Pi 4/Pi 5 are excluded from
  MPEG-2 HW.

---

## 7. Bandwidth premise — **CORRECTED**

The "raw-frame streaming fails above ~320×240 due to gigabit" claim is **not** a
bandwidth fact:

| Raw case (W×H×24bpp×60) | Mbps | % of 1 GbE | Fits? |
|---|---|---|---|
| 320×240 | 110.6 | 11% | yes |
| 720×480 (NTSC) | 497.7 | ~50% | yes |
| 720×576 (PAL) | 597.2 | ~60% | yes |
| 1920×1080 | 2,986 | ~299% | **no** |

The documented practical wall is **~720×480i** (Groovy/MiSTerCast README: "nothing
over 720×480i recommended… due to throughput on MiSTer"; forum instability
>~200 Mbps) — an **FPGA receive/blit-throughput + UDP-jitter** limit, *right at* the
480i target. 1080p60 raw (~3 Gbps) is the case that truly exceeds gigabit.

**Net (the architecture still wins, for the accurate reasons):** the raw path is
throughput-limited *exactly at* your 480i target and is a jitter-sensitive ~0.5 Gbps
firehose; software MPEG-2 decode on the **weak console A9** is the thing to avoid;
the analog path is 24-bit; the DVD-dump PS passthrough preserves true field cadence.
Sending **compressed** video and decoding in fabric sidesteps all of it.

---

## 8. Source URLs

- `https://opencores.org/projects/mpeg2fpga` · `https://github.com/OldRepoPreservation/mpeg2fpga` · `http://www.kdvelectronics.eu/mpeg2fpga/mpeg2fpga.html`
- `https://github.com/mrchrisster/MiSTer_MPEG2`
- `https://github.com/MiSTer-devel/CDi_MiSTer` · `https://github.com/Slamy/CDi_MiSTer`
- `https://github.com/mrchrisster/mister_cdi_vcd_creator`
- `https://github.com/MiSTer-devel/Main_MiSTer` (`user_io.cpp`, `fpga_io.cpp`, `scaler.cpp`) · `https://github.com/MiSTer-devel/Template_MiSTer` (`sys/hps_io.sv`, `sys/sys_top.v`, `sys/sd_card.sv`)
- `https://github.com/MiSTer-devel/PSX_MiSTer` (`PSX.sv`) · `https://github.com/MiSTer-devel/Saturn_MiSTer` (`Saturn.sv`)
- Pi 5: `https://www.raspberrypi.com/products/raspberry-pi-5/` · `https://codecs.raspberrypi.com/mpeg-2-license-key/`
- Groovy/MiSTerCast: `https://github.com/psakhis/Groovy_MiSTer` · `https://github.com/iequalshane/MiSTerCast`
- Hardware: `https://retroremake.co/products/superdock` · `https://www.analog.com/en/products/adv7125.html` · `https://www.cnx-software.com/2025/01/29/superstation-one-soc-fpga-based-retro-gaming-console-supports-mister-emulation-playstation-controllers-cd-drive/`

### Caveats / what could not be confirmed
- No **working, verified** MPEG-2 core on Cyclone V exists publicly (only the
  unproven `MiSTer_MPEG2`).
- `mpeg2fpga` interlaced **output** is supported; **field-exact** cadence end-to-end
  on DVD content is **unproven** (validation item).
- Exact BSD variant of `mpeg2fpga` (2- vs 3-clause) not pinned verbatim.
- Saturn CD virtual-disk index (=0) inferred from array ordering.
- SuperDock optical-as-OS-block-device: no public evidence either way (parked).
