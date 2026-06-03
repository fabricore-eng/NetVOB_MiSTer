# Risk register

Ranked by **(likelihood × impact)** on the project's success. The biggest reordering
versus the original brief: the **FIFO injection seam is now LOW** (it's proven by
`MiSTer_MPEG2` + the CD-i core), while **decoder bring-up is the new #1** (the
prior-art "80% done" premise did not survive verification — see
[`findings.md`](findings.md) §1).

| # | Risk | Likelihood | Impact | Score |
|---|------|------------|--------|-------|
| 1 | Decoder bring-up to confirmed, stable video-out | High | Critical | 🔴 highest |
| 2 | Field-cadence / true-480i preservation | Med–High | High | 🔴 |
| 3 | `mpeg2fpga` Xilinx→Cyclone V resource/timing fit | Medium | High | 🟠 |
| 4 | A/V sync over the network | Medium | High | 🟠 |
| 5 | Seek across GOP boundaries | Medium | Medium | 🟠 |
| 6 | Device target / board-support + core-template / CMA / DDR3 | Medium | High | 🟠 |
| 7 | Transcode CPU under Plex contention | Medium | Medium | 🟡 |
| 8 | Plex private-API stability | Medium | Low–Med | 🟡 |
| 9 | Upstream dependency (mrchrisster / Slamy) | Low–Med | Medium | 🟡 |
| 10 | FIFO injection seam | Low | High | 🟢 (de-risked) |
| P | SuperDock optical OS-exposure | n/a (future) | n/a | ⚪ parked |

---

### 1. 🔴 Decoder bring-up to confirmed, stable video-out — **highest**
`MiSTer_MPEG2`'s **data path is hardware-verified but its video pipeline is not** —
own notes cite FSM/`waitrequest` hangs, `mem_shim` "not yet compiled," "no full
video output confirmation," and no release. The whole project rests on this.
- **Mitigation:** make it **M1a**, before anything else. Reproduce the referenced
  "prior working config" first, then forward-fix. Engage upstream early. Keep the
  CD-i core as a known-good MPEG decode reference.
- **Contingencies if it can't be made to work:** (a) more thorough re-port of
  `mpeg2fpga` (decision D1 alt); (b) fall back to a **progressive 480p** bring-up to
  prove the pipeline, defer interlaced; (c) worst case, rescope to the CD-i MPEG-**1**
  path for a demo while the MPEG-2 decoder matures. Decide at the M1 gate.

### 2. 🔴 Field-cadence / true-480i preservation
`mpeg2fpga` supports interlaced **output**, but **field-exact** reproduction of DVD
content (field-picture order, **3:2 pulldown**, parity through seek) is **unproven**;
`MiSTer_MPEG2` currently hardcodes a 27 MHz SD clock and "VSYNC-paces" (PAL files
fast-forward).
- **Mitigation:** treat field-exactness as **M7** with explicit field-parity
  tracking from M1 (don't bake in progressive-only assumptions). Verify with a
  capture/scope. Preserve disc cadence in DVDDumpSource (passthrough already does);
  pick matching encoder field settings in PlexSource. Validate the 480i modeline into
  the ADV7125.

### 3. 🟠 `mpeg2fpga` Xilinx→Cyclone V resource/timing fit
The decoder is a 2007 **Virtex-5** design (~50% of an XC5VLX50T at 75 MHz). Cyclone V
has different BlockRAM shapes, multipliers, and DDR latency.
- **Mitigation:** `MiSTer_MPEG2` already crossed much of this (DDR3 via f2sdram, CMA
  mapping) — inherit it. Target **SD/480i first** (4 MB frame store, far easier than
  the 16 MB HD mapping). Budget Quartus fitting/timing-closure iterations.

### 4. 🟠 A/V sync over the network
Audio decoded on the ARM (D2) must stay lip-synced to a VSYNC-paced video master
across network jitter, buffering, and seeks.
- **Mitigation:** PS keeps audio+video on a common PTS base (less drift to begin
  with); slave audio to a **core-exposed video presentation clock**; resample-slew for
  small drift, PTS resync after seek/underrun; fixed end-to-end latency budget. Wired
  GbE + ARM ring buffer absorb jitter. Hardened in **M6**.

### 5. 🟠 Seek across GOP boundaries
Clean seeks require landing on an I-frame and resetting decoder references without
artifacts or wrong field parity.
- **Mitigation:** DVDDumpSource uses **VOBU/DSI** nav for exact GOP seek points;
  PlexSource snaps to `ffmpeg` keyframes. Flush all buffers + decoder I-frame reset on
  seek; resync A/V from first post-seek PTS. **M6**.

### 6. 🟠 Device target / board-support + core-template / CMA / DDR3
**⚠️ Build-critical:** the SuperStation is Cyclone V SX `5CSXFC6D6F31I7N` (896-pin),
*not* the DE10-Nano's SE `5CSEBA6U23I7` (672-pin). Bitstreams are part/pin-specific,
so DE10-Nano `.rbf` won't load as-is and the sibling project's device/`sys.tcl` target
**does not transfer** — building for the SuperStation needs its board-support `sys/`
(pins, PLLs, ADV7125 wiring), which is not publicly published. Plus the usual 24 MB
CMA, shared DDR3, and one-PLL clock discipline, and fitting on the part.
- **Mitigation:** **develop on a DE10-Nano** (the entire sibling toolchain transfers
  verbatim; `MiSTer_MPEG2` already targets it) and treat the SuperStation as a late
  **port** step; obtain board-support from Retro Remake / Taki Udon for the final
  target. Inherit `MiSTer_MPEG2`'s CMA/DDR3 mapping (`{7'b0011000, addr}` @
  `0x30000000`); SD frame stores are small; stay within `DDRAM_*`/`hps_io` conventions;
  one PLL from the start. Resolve the device decision in **M0**.

### 7. 🟡 Transcode CPU under Plex contention
Pi 5 has **no hardware encoder**; a 480i MPEG-2 software encode is fine **alone**, but
contends with a concurrent Plex (software) transcode for the same A76 cores.
- **Mitigation:** active cooling; cap/parameterize the MPEG-2 encode (bitrate,
  ME effort); detect contention and degrade gracefully (lower bitrate) or queue;
  document that simultaneous heavy Plex transcoding + NetVOB Plex playback may
  saturate the CPU. DVDDumpSource (passthrough) is unaffected.

### 8. 🟡 Plex private-API stability
Parts of the Plex API used for browse/file access are undocumented and can change.
- **Mitigation:** isolate **all** Plex specifics in `sources/plex/`; pin a known-good
  API client; degrade to sidecar/local-path access; PlexSource failure never affects
  the DVD spine (separate libraries).

### 9. 🟡 Upstream dependency (mrchrisster / Slamy)
Building on `MiSTer_MPEG2` (and referencing the CD-i core) ties us to others' WIP.
- **Mitigation:** fork and vendor a pinned commit; engage maintainers (mrchrisster
  authors both the port and the VCD creator); keep our integration notes in
  `core/README.md`; contribute fixes upstream where possible.

### 10. 🟢 FIFO injection seam — **de-risked**
Originally feared; now **low**. The `sd_*` sector seam is used by the CD-i core
(`hps_cd_sector_cache.sv`) and **hardware-verified** in `MiSTer_MPEG2`
(`mpg_streamer.sv` loaded 5500 sectors). Compressed bitrate (<2 MB/s) is far below
the path's capacity.
- **Mitigation:** reuse the proven pattern; keep the DDR-ring seam (#2 in findings) as
  an escape hatch only if latency/buffering demands it.

---

### P. ⚪ Parked / future — SuperDock optical OS-exposure
Whether the SuperStation firmware exposes the SuperDock DVD-RW as a generic OS block
device (vs. only via forked per-system cores) is **publicly undocumented**. **Only
relevant to the future DiscSource**, which is out of scope now.
- **Action:** none until the future tier is taken up. Revisit when/if a SuperDock is
  in hand. Does **not** gate M0–M7.

---

## Watch-items (not yet risks, verify when relevant)
- Exact **BSD variant** of `mpeg2fpga` (2- vs 3-clause) before any redistribution.
- Zaparoo's concrete **launch-command surface** on the SuperStation (affects M4 NFC).
- A core-exposed **vblank/frame counter** for A/V sync may be a small RTL addition
  (M6) — confirm none exists already.
- 480i **modeline** correctness into the ADV7125 (M7).
- **Verification discipline** ([`dev-workflow.md`](dev-workflow.md) §5): sim
  single-writer per trace dir; **reproduce before claiming a milestone** (a green sim
  once ≠ a result). Guards against false-positive milestone claims — the same "flag
  unverifiable claims" ethos applied inward.
