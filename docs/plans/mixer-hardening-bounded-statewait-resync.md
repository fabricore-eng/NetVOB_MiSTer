# Plan — mixer underrun hardening (bounded-STATE_WAIT re-sync)

**Status:** designed + adversarially reviewed (Plan agent, 2026-07-03), NOT yet implemented.
**Scope:** sim-validatable, no FPGA build required to prove it. Follow-up to the display-ack-wedge
root-cause ([[display-wedge-pal-modeline-artifact]], progress.md #10, handoff docs/handoffs/dvd.md).
**Why:** with the HW-matched NTSC modeline the permanent frame-4 wedge is gone, but the mixer's
underrun-recovery is still fragile — under harsh memory latency it parks in `STATE_WAIT` on a
top-of-frame line-start for up to a full field (not draining), throttling/stuttering decode. This
hardens that. It is a **recovery** (accept a brief glitch during a real underrun) not a prevention
of the underrun (which would mean touching memory latency — out of scope, forbidden to touch the bus).

## The bug, RTL-exact
- `mixer.v` STATE_WAIT holds `pixel_rd_en=0` (mixer.v:187) and only exits on
  `pixel_en_in && display_first_pixel` (mixer.v:123). `display_first_pixel` (mixer.v:112-114) needs
  `h_pos==0` AND a `v_pos` matching the stored line-start class: `ROW_0_COL_0`→v_pos==0,
  `ROW_1_COL_0`→v_pos==1, `ROW_X_COL_0`→v_pos∉{0,1}.
- After a mid-line underrun (STATE_PIXEL→STATE_INIT, mixer.v:131) the mixer re-searches and may store
  a **top-of-frame** code (`ROW_0/1_COL_0`) while the raster is mid-field → STATE_WAIT dwells up to
  ~one field waiting for the raster to wrap to v_pos 0/1. Not draining ⇒ pixel_queue fills ⇒
  `pixel_wr_almost_full` ⇒ resample_bilinear/resample_dta stall ⇒ disp fifos fill ⇒ `do_disp=0` ⇒
  resample_addrgen STATE_WAIT ⇒ `output_frame_rd` never pulses ⇒ picbuf stuck ⇒ `vld_en=0`. Pure
  backpressure; `LOST_AT_SHIM=0`.
- Bounds (NTSC 480i default, modeline.v:192-201, syncgen.v): one field =
  `(HORZ_LEN+1)×(VERT_LEN+1) = 859×263 = 225,917` dot_clks; **clean-path** STATE_WAIT dwell ≤ one line
  `= 859` dot_clks (exits at the next `h_pos==0`). `v_pos` LSB is field parity (syncgen.v:289:
  `v_pos <= interlaced ? {v_cntr_2[10:0], ~odd_field} : v_cntr_2`) — v_pos==0 only on odd (top) field,
  v_pos==1 only on even (bottom) field. `pixel_repetition=0` in this config (VID_MODE=3'b001) → the
  three REPEAT states are dead; do not rely on them.

## The fix (both mixer.v copies)
The sim compiles the **pristine** `core/mpeg2fpga/rtl/mpeg2/mixer.v` (memshim Makefile RTL =
`../../mpeg2fpga`); the HW build uses the **fork** `core/MiSTer_MPEG2/rtl/mpeg2/mixer.v` (identical
except a `clk_en` gate on every register). Apply the SAME logic to both — ungated in pristine,
`clk_en`-gated in fork. Total change: 1 parameter, 2 counters + 1 wire, 1 added next-state else-if,
1 added clause in the `position_in_0` register.

1. **Params/regs** (by the STATE_ params and `position_in_0` decl):
   ```
   parameter [17:0] WAIT_TIMEOUT_N = 18'd131071;   // ~0.58 field; N ≫ 859 (clean dwell), ≪ 225917 (field)
   reg  [17:0] wait_dwell;                          // STATE_WAIT dwell (dot_clks)
   reg  [15:0] mixer_resync_cnt;                    // LOUD: # resyncs; MUST be 0 on clean runs
   wire        wait_timeout = (wait_dwell == WAIT_TIMEOUT_N);
   ```
2. **Dwell counter** (new always block; saturating so `wait_timeout` stays asserted until STATE_WAIT
   is left). Fork (gated):
   ```
   if (~rst) wait_dwell <= 0;
   else if (clk_en && (state==STATE_WAIT) && ~wait_timeout) wait_dwell <= wait_dwell + 1;
   else if (clk_en && (state!=STATE_WAIT))                  wait_dwell <= 0;
   else wait_dwell <= wait_dwell;
   ```
   Pristine: drop the `clk_en &&` guards (match file style).
3. **Next-state** — modify only the STATE_WAIT arm (normal exit keeps priority):
   ```
   STATE_WAIT: if (pixel_en_in && display_first_pixel) next = STATE_FIRST_PIXEL;
               else if (wait_timeout)                   next = STATE_INIT;   // bounded resync
               else                                     next = STATE_WAIT;
   ```
4. **Clear the stale latch on timeout** — extend the `position_in_0` register, timeout clause FIRST:
   ```
   if (~rst) position_in_0 <= ROW_X_COL_X;
   else if (clk_en && (state==STATE_WAIT) && wait_timeout) position_in_0 <= ROW_X_COL_X;  // drop stale line-start
   else if (clk_en && pixel_rd_valid)                      position_in_0 <= position_in;
   else position_in_0 <= position_in_0;
   ```
   This is the key to avoiding the re-park trap: with `position_in_0=ROW_X_COL_X`, `first_pixel_read`
   loses its sticky term, so STATE_INIT's `pixel_rd_en <= ~pixel_rd_en && ~first_pixel_read` toggles
   and **actually drains** until the queue presents a fresh real line-start — which then re-locks a
   code whose `display_first_pixel` matches near the current raster line (recovers within ≤ a line or
   two). `display_first_pixel` itself is UNCHANGED (preserves interlace parity — parity is inherited
   from the producer's line-start codes, never from the mixer).
5. **Resync counter** (new always block): increment when `(state==STATE_WAIT) && wait_timeout`.

Do NOT: drain at every vsync (discards legit queued pixels → tv_out not byte-identical), reset the
pixel_queue read pointer (CDC hazard on the gray-code dual-clock FIFO), or add a picbuf ack timeout
(tears frames / no-op). Those were the 3 prior auto-fixes, all adversarially found broken.

## Why it's inert on the clean path (byte-identical)
`wait_dwell` counts only in STATE_WAIT and resets on leaving it; clean dwell ≤ 859 ≪ N=131071, so
`wait_timeout` is 0 forever on a clean run ⇒ the added else-if and position_in_0 clause are dead ⇒
`state`, `position_in_0`, `first_pixel_read`, `display_first_pixel`, `pixel_rd_en`, and every output
register are bit-identical ⇒ framestore per-slot Y-planes AND tv_out_*.ppm are byte-identical. The
counters are pure observers (fan out only to the report). No new almost_full/backpressure toward
framestore/f2sdram ⇒ cannot stall the bus; on fire it only drains sooner (relieves pressure).

## Sim validation gate (from core/sim/memshim; each iter = a few min, no FPGA build)
`make build` (compiles pristine mixer + fix). Add `mixer_resync_cnt` to the `[mix]` heartbeat + stall
report (probe-only tb edit). Then:
- **A baseline** `./run_memshim.sh run_baseline 3 600 -- +ddr_rd_latency=30` — GATE: `mixer_resync_cnt`
  == 0 throughout; per-slot Y-md5 (extract_framestore_slots.py) == a pre-fix reference; tv_out_*.ppm
  == pre-fix reference (byte-identical).
- **A0 zero-latency** `-- +ddr_zero_latency` — same checks, strongest determinism.
- **B harsh** `-- +ddr_rd_latency=30 +ddr_wait_period=8 +ddr_rd_jitter=7` — GATE: NO
  WATCHDOG-NO-PROGRESS stall; decode keeps advancing (`[traj] mb/fr` rising, `[pix] prd` rising);
  `mixer_resync_cnt` > 0 but MODEST (a handful/episode, not thousands — thousands ⇒ raise N);
  `LOST_AT_SHIM=0`; ≥ MIN_FRAMES frames produced. Pre-fix this config wedges (watchdog fires).
- **Both copies:** `diff` the two mixer.v files → must differ ONLY by the `clk_en` port/gates (same
  diff shape as today, plus the gated/ungated new blocks). Lint the fork copy.

## Risks (ranked) + tuning
- **R1 N too small** → non-zero baseline resync (gate catches it). Margin is 150× (859 vs 131071).
- **R2 N too large** → longer stall before firing; N=131071 dot_clks = 524k mem_clks ≈ 0.26× the 2M
  watchdog, fires well before it; drop to 2^16 (65535, ~76× clean margin) if Run B lands too close.
- **R3 thrash** (resync→re-park→resync) → bounded to ~1/field (the recovery cadence we want); gate
  R-B "modest count" catches pathological thrash.
- **R4 clk_en asymmetry** between copies → the diff-shape check + fork lint catch a missing/extra gate.
- Honest residual: this is recovery, not prevention — under harsh latency you get a brief stutter +
  recover instead of a field-long freeze. Correct layer given the no-touch-the-bus constraint.

## Landmarks
- `core/mpeg2fpga/rtl/mpeg2/mixer.v` (SIM copy — edit ungated) · `core/MiSTer_MPEG2/rtl/mpeg2/mixer.v`
  (HW copy — edit clk_en-gated). FSM: STATE_WAIT/pixel_rd_en at ~:120-131,:182-195; `first_pixel_read`
  /`display_first_pixel` at :110-114; `position_in_0` reg at ~:176-179.
- `core/sim/memshim/tb_memshim.v` — add `mixer_resync_cnt` to the `[mix]` heartbeat + stall report.
- `core/mpeg2fpga/rtl/mpeg2/modeline.v` (HORZ_LEN/VERT_LEN/VID_MODE) · `syncgen.v` (v_pos parity).
- `tools/build/extract_framestore_slots.py` — per-slot Y-md5 byte-identical gate.
