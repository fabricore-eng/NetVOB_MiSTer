# Handoff — dvd — 2026-07-03 (late)
Branch: feat-decoder-bringup   ·   Repo: ~/Dev/fabricore/NetVOB_MiSTer   ·   HEAD: c7b220e

## TL;DR
The decode-correctness milestone remains **MET on HW** (frame-0 SSIM 0.9918). This session
took up the **display-ack wedge** (the parked "scanout freeze ~frame 4" rung) and root-caused
it — with a surprise: **the permanent frame-4 *decoder freeze* reproduced in the memshim
co-sim was substantially a SIM-CONFIG ARTIFACT (a PAL/NTSC modeline mismatch), not a real
silicon decode block.** Fixed the sim harness; decode is byte-identical. A real but
NON-decode-blocking residual (mixer underrun fragility) is scoped as a follow-up. Committed +
pushed (c7b220e). No board used, no locks held.

## What was established this session (all empirically probe-verified)
- **Instrumented the co-sim** (`core/sim/memshim/tb_memshim.v`): added the full display-path
  probe set to the periodic heartbeat + stall report — `resample_dta.state`,
  `resample_bilinear.state`, `disp_reader` fifo occupancy, pixel_queue producer/consumer
  counters, and the **mixer** state + raster (`h_pos`/`v_pos`/`pixel_en`/`position_in_0`). This
  is the "one probe deeper via mpeg2.resample.*" the prior handoff flagged. Reusable kit.
- **Freeze chain, empirically mapped** (matches the multi-agent workflow forensics exactly;
  `LOST_AT_SHIM=0` throughout → pure backpressure, NOT a pairing desync — the old §FINAL
  "disp addr/data resync" framing was off): mixer parks in **STATE_WAIT** `pixel_rd_en=0`
  (stored `position_in_0=ROW_0_COL_0`, `display_first_pixel` never satisfied) →
  `pixel_wr_almost_full` → resample_bilinear STATE_INIT → resample_dta STATE_READY →
  `disp_wr_dta_almost_full` → `do_disp=0` → `disp_wr_addr_almost_full` → resample_addrgen
  STATE_WAIT → `output_frame_rd` never pulses → picbuf stuck STATE_IP_FRAME_0 → motcomp_busy →
  `vld_en=0` → decode freeze at frame 4.
- **HEADLINE:** the permanent wedge is a **PAL/NTSC modeline mismatch**. The memshim `Makefile`
  forced `MODELINE_PAL_INTERL` (576-line raster) against the 720x480 **NTSC** clip; a stored
  `ROW_0_COL_0` can never satisfy `display_first_pixel` (needs `h_pos==0 && v_pos==0 &&
  pixel_en` co-incident) on the wrong-geometry raster → mixer parks forever. The **HW build's
  `core/MiSTer_MPEG2/rtl/mpeg2/modeline.v` DEFAULTS to `MODELINE_NTSC_INTERL`** (matched), so
  silicon never sees the mismatch. Rebuilt sim with NTSC → **decodes 8+ frames, no permanent
  wedge** (mixer self-heals each field).
- **FIX (this session):** `core/sim/memshim/Makefile` now defaults
  `MODELINE ?= MODELINE_NTSC_INTERL` (+ comment). **Decode byte-identical** across modelines —
  all 4 framestore slots hash-match PAL-vs-NTSC (`extract_framestore_slots.py`); modeline feeds
  only syncgen/display, never the decode data path.
- **RESIDUAL (real, NOT decode-blocking):** under HARSH latency (`rd_lat=30 wait=8 jitter=7`)
  even matched NTSC goes very sluggish (long STATE_WAIT parks) but always recovers (no permanent
  stall). The mixer's underrun-recovery is genuinely fragile — a HW smoothness-margin item.

## Next steps (ranked)
1. **(Optional robustness) bounded-STATE_WAIT re-sync in `mixer.v`.** The mixer's clean-path
   STATE_WAIT dwell is ≤ ~1 line; a broken alignment dwells up to ~1 field. Add a dwell counter:
   if STATE_WAIT persists > N line-times (N ≫ 1 line, ≪ 1 field), force a drain/re-search so the
   queue keeps draining and never backpressures decode into a freeze — LOG+count the resync
   (doctrine #3, never mask). Inert on the clean path (N never trips) → byte-identical. NB the
   prior workflow's 3 fixes (decouple-picbuf / level-ack / vsync-drain-every-field) were ALL
   adversarially found RISKY/BROKEN — do not resurrect them verbatim. The mixer FSM differs
   between the sim (pristine `core/mpeg2fpga`) and HW (fork `core/MiSTer_MPEG2`, only a `clk_en`
   gate) — apply to both; validate in sim (harsh NTSC must no longer permanently stall + decode
   byte-identical), never build for it alone.
2. **The REAL HW scanout symptom** (black display / OSD-squish) is the SEPARATE video-timing /
   interlace bug — see [[scanout-blind-spot-ddr-vs-crt]]. Triangulate via the Frank-menu test +
   an HDMI OSD grab; this is decode-independent and is NOT the freeze above.
3. **Audio / A-V sync / PS→ES demux** (real DVD VOBs are program streams; current ingest is ES).

## Reproduce / validate
- Wedge (artifact): `cd core/sim/memshim && make build MODELINE=MODELINE_PAL_INTERL &&
  ./run_memshim.sh run_pal 9 900 -- +ddr_rd_latency=30` → permanent STALL at frame 4.
- Healthy (HW-matched): `make build && ./run_memshim.sh run_ntsc 9 900 -- +ddr_rd_latency=30`
  → 8+ frames, no permanent stall. (`make build` now defaults NTSC.)
- Byte-identical: `python3 tools/build/extract_framestore_slots.py <run>/framestore_0000.ppm
  <out>` → all 4 slot Y-md5s match across modelines.

## Landmarks
- `core/sim/memshim/tb_memshim.v` — display-path probes: `[pix ...]`/`[mix ...]` heartbeats +
  the extended stall report (mixer/resample_dta/pixel_queue block).
- `core/sim/memshim/Makefile:37` — `MODELINE ?= MODELINE_NTSC_INTERL` (the harness fix).
- `core/MiSTer_MPEG2/rtl/mpeg2/mixer.v:120-131` — the STATE_WAIT / `display_first_pixel` /
  underrun-abandon logic (the fragility origin). `resample_addrgen.v:186,222` (STATE_WAIT +
  the pulse-only `output_frame_rd`). `motcomp_picbuf.v:145` (the level-latched valid it acks).
- `docs/progress.md` #10 — the detailed trail. Memory: [[display-wedge-pal-modeline-artifact]].

## Open questions / risks
- **The mixer FSM differs sim-vs-HW** (only `clk_en`); a mixer fix must land in both and the sim
  (pristine) is what validates it. Decode-path FSMs (resample*, picbuf, framestore*) are byte
  identical across the two trees.
- The sim's dot_clk free-runs; HW paces the mixer via `dot_ce`. Underrun dynamics may differ
  slightly on HW — treat the sluggishness threshold as indicative, not exact.
- Never re-introduce a modeline mismatch in the sim: keep `MODELINE` matched to the clip/HW.
