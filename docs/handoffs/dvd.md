# Handoff — dvd — 2026-07-03 (latest)
Branch: feat-decoder-bringup   ·   Repo: ~/Dev/fabricore/NetVOB_MiSTer

## TL;DR
**Root-caused the #1 open blocker — the CRT "black video" scanout bug — and reproduced it OFFLINE in the
memshim sim.** It is **display-read starvation under memory latency**, NOT interlace/HALFLINE, NOT decode,
NOT the mixer FSM. Live-triangulated with the human on the CRT (he set the SuperStation to `direct_video=1`):
the decoder writes a **correct, detailed frame to DDR** (rendered clean color video), the raster runs (OSD
is **full-height, not squished** — so the handoff's "fix interlace/HALFLINE" lead is **wrong/moot**), yet the
video area is **pure flat black**. The display pipeline (framestore→resample→pixel_queue→mixer) can't feed
one pixel/dot-clock in real time under f2sdram latency → the pixel_queue underruns → the mixer emits its
default **Y=16,U=V=128 = pure black** (mixer.v:221). Decode is unaffected (no deadline; it just runs slower).
**Confirmed the lever + quantified it; designed the fix; did NOT implement it** (it's a careful cross-module
change to a placement-marginal core — deserves a focused validated cycle). RTL tree is clean (experiment reverted).

## What this session established (empirically; sim + live HW with the human)
- **Isolation is airtight.** DDR framestore dump (`/dev/mem 0x30000000`) renders a clean color test pattern
  (SSIM 0.92 = animated-clip phase floor). UART healthy (`P≈RP`, `VL/BN` nonzero). CRT = pure flat black +
  a **slightly color-tinted** framework OSD (the human: "right-ish but tinted/shifted" → output/DAC path works;
  minor component/YPbPr level thing, deferred). So: decode✓, memory-bus✓, output-path✓, **display-readout✗**.
- **Black mechanism (mixer.v):** emits real pixels only in `displaying` states; else Y=16/U=128/V=128 = black
  (mixer.v:219-232). Leaves STATE_INIT only on `first_pixel_read = pixel_rd_valid && first-pixel-position`
  (mixer.v:110). Empty pixel_queue → stuck in STATE_INIT → pure black. Mid-line `pixel_rd_underflow` →
  STATE_INIT → rest of line black (mixer.v:131,136) — one underflow blanks the whole field (fragile).
- **Sim reproduces it (the oracle).** `core/sim/memshim` dumps `tv_out_*.ppm` (the mixer's scanout). Peak
  field brightness **collapses with `+ddr_rd_latency`**: lat30→**74.7**, lat60→42, lat120→**13.5**, lat240→9.2
  (≈black). At lat240 only the first ~140px of top lines render then underrun→black = the CRT symptom.
- **Bottleneck = the outstanding-read throttle `READ_LIMIT=4`** (`mem_shim.sv:144`), which gates ALL reads at
  `outstanding_reads>=4` and is **shared decode+display**. The HPS f2sdram bridge **LOCKS at 6 outstanding**
  (mem_shim.sv:139-141) → **max HW-safe READ_LIMIT = 5**. Quantified at lat120: `READ_LIMIT 4→13.5, 5→18.1,
  8→45.5` (need ~74 for clean). **The simple bump to 5 is INSUFFICIENT** (still mostly black). The display
  needs *effectively* more read throughput than a shared-5 budget provides.
- **Throughput math** (why): display needs ~1.9M reads/s (720×480 YUV420, burstcnt=1, 8B/read, 30fps).
  Available = `READ_LIMIT × 108MHz / latency`, *shared with decode*. Under HW latency the shared-4 budget
  starves the real-time display while deadline-free decode still completes. Bandwidth is fine (~864MB/s peak
  ≫ ~15MB/s needed) — it's a **latency × outstanding-reads** problem, mem_shim uses `ddr3_burstcnt=1`.

## Corrected mental model (supersedes the prior handoff)
The prior lead ("triangulate then fix syncgen HALFLINE/interlace") is **retired**: under direct_video the OSD
is full-height (interlace geometry is fine). HALFLINE=0-vs-428 is a **separate** analog-half-line question
(and `vga_out.sv` provably does NO weave — so IF the analog interlace ever needs fixing, 428 is right for the
raw-DAC path — but that is NOT the current black-video bug). The prior "mixer STATE_WAIT" story is also not it
([[mixer-hardening-falsified-field-top-wait]]). The real bug is **memory-pacing starving the DISPLAY client**
— which the last handoff had listed as an *optional* "only if a smoothness problem is observed" item. It is the
PRIMARY blocker.

## Next steps (ranked) — the FIX (design done, implementation pending)
1. **Reserved read credits for the display client (preferred, HW-safe).** Give the real-time display dedicated
   outstanding-read slots and throttle the deadline-free decode, keeping TOTAL ≤5 (below the 6-lock). e.g.
   decode throttled at `outstanding>=3`, display allowed up to 5 → display always has ≥2 slots decode can't
   take. **Requires cross-module plumbing:** the mem_shim throttle is BLIND (`mem_req_rd_cmd[1:0]` = only
   NOOP/REFRESH/READ/WRITE, mem_shim.sv:7,41-44) — add an `is_display_read` bit from `framestore_request.v`
   (it knows `do_disp` vs `do_fwd/do_bwd`, priority scheme :466-486) → mpeg2video port → emu.sv wiring →
   mem_shim throttle. VALIDATE in the sim oracle at lat120/240 (target: peak field mean back toward ~74) BEFORE
   a build. Watch: don't starve decode so hard it can't produce frames; keep the resp_timeout/wedge recovery intact.
2. **Alternative — display read bursting** (`ddr3_burstcnt>1` for display): N words/read → N× throughput per
   outstanding slot, stays HW-safe on count. More robust but bigger (framestore tiling means scanlines aren't
   contiguous — burst within a macroblock row). Bigger change; consider if reserved-credits proves insufficient.
3. **Combine with:** deeper `pixel_queue`/DISP FIFOs + prefetch-during-blank + priming the mixer (don't paint
   until the queue fills) so a single underflow doesn't blank a whole field (mixer.v:131,136). Secondary.
4. **After the black is fixed:** the minor OSD color tint (component/YPbPr level) and then audio / A-V sync.

## Reproduce / validate (sim-only, from core/sim/memshim; each iter ~1-2 min, NO FPGA build)
- Repro the black: `make build && ./run_memshim.sh run_lat240 4 300 -- +ddr_rd_latency=240` → render
  `run_lat240/tv_out_*.ppm`; peak field mean ≈9 (black). Baseline `+ddr_rd_latency=30` → ~74 (clean picture).
- Brightness metric (per field, drop the last partial ppm): `Image.convert('L')` histogram mean; "content"
  fields reach mean ~74 when healthy, collapse to <15 when starved.
- Lever check (already done): edit `mem_shim.sv:144 READ_LIMIT`, `make build`, rerun at lat120, compare peak
  mean. 4→13.5, 5→18.1, 8→45.5 (8 is HW-UNSAFE, ceiling reference only). **Revert to 4 after** (done).
- Gate telemetry: `grep '\[pix' run_*/run.log` shows `do_disp`, `pix_rd_empty`; `[traj]` shows decode mb/frame
  (at lat240 decode also crawls — lat240 over-stresses; lat90-120 is the "decode-fine, display-starved" regime).

## Landmarks (file:line)
- `core/MiSTer_MPEG2/rtl/mpeg2/mixer.v:110,131,136,219-232` — black-emit + underflow→STATE_INIT fragility.
- `core/MiSTer_MPEG2/rtl/mpeg2/framestore_request.v:466-486` — mem-client priority (display is HIGH prio) +
  the `do_disp` gate (`~mem_req_wr_almost_full && ~tag_wr_almost_full`, :480).
- `core/MiSTer_MPEG2/rtl/mem_shim.sv:139-146` — READ_LIMIT=4 throttle + the 6-outstanding bridge-lock note.
- `core/MiSTer_MPEG2/rtl/mpeg2/fifo_size.v:34-35,158-177` — FIFO depths/thresholds ("scale threshold with
  latency"); `PIXEL_DEPTH=10`, `DISP_DTA_DEPTH=8`, `DTA_THRESHOLD=64`.
- `core/MiSTer_MPEG2/rtl/emu.sv:372-459,510-539` — mpeg2video+mem_shim instantiation, VGA output wiring.
- `core/MiSTer_MPEG2/rtl/mpeg2/modeline.v:137-155` — NTSC_INTERL, HALFLINE=0 (interlace, separate issue).
- `core/sim/memshim/` — the oracle (run_memshim.sh, tb_memshim.v telemetry, ddr3_model.v latency knobs).

## Board / setup notes
- **the human set the SuperStation (mister) to `direct_video=1`** (component-video → his CRT; `vga_scaler=0`).
  So the core owns 100% of timing; no HDMI capture on mister (only de10 has a capture card). Keep direct_video.
- Deployed core = `mpeg2fpga_dvd_staticgate.rbf` (HALFLINE=0). Board released, no locks held.
- Memories: [[scanout-black-display-read-starvation]] (this root cause), [[scanout-blind-spot-ddr-vs-crt]],
  [[feed-gate-solved-mgl-abspath]] (READ_LIMIT=4 / bridge-lock origin), [[bridge-placement-marginal-root-cause]].

## Watch out
- **READ_LIMIT must stay <6 on HW** (bridge locks at 6). Never ship READ_LIMIT=8 (that was a sim-only ceiling test).
- **Placement-marginal core:** any netlist change can re-roll the f2sdram bridge placement. Prior rushed fixes
  to this core repeatedly backfired (3 broken auto-fixes; falsified mixer-resync; HW-stalling gap-fix). Validate
  in sim first; keep changes minimal; expect to re-verify the decode gate after any RTL change.
- Do NOT re-chase HALFLINE/interlace or the mixer-resync for the black video — proven not the cause this session.
- Keep the sim MODELINE matched to the clip (NTSC), never PAL.
