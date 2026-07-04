# Handoff — dvd — 2026-07-03 (latest)
Branch: feat-decoder-bringup   ·   Repo: ~/Dev/fabricore/NetVOB_MiSTer

## TL;DR
The decode-correctness milestone remains **MET on HW** (frame-0 SSIM 0.9918). This session took the
planned immediate action — **implement the mixer bounded-STATE_WAIT re-sync** — implemented it exactly
per the plan in BOTH mixer.v copies, validated it **sim-only**, and the validation **FALSIFIED the
plan**: the fix corrupts the *clean* path (fires ~2×/frame even at zero latency) because the plan's
premise ("clean STATE_WAIT dwell ≤ 859 dot_clks") is empirically false — the mixer legitimately parks
up to ~1.77 fields at every frame boundary waiting for the field-top raster line. Clean and harsh peak
dwells are *identical* (~401k dot_clks) ⇒ no threshold can separate them. The fix was **cleanly
reverted** (tree byte-identical to pre-fix reference); the plan doc is banner-marked ⛔ FALSIFIED.
**Net: there is no mixer-FSM bug to fix here** — the "mixer underrun fragility" the prior handoff
flagged is the mixer *correctly* waiting for the raster while the decoder is briefly memory-starved. No
board used, no locks held.

## What this session established (all empirically probe-verified, sim-only)
- Implemented the fix per plan (saturating `wait_dwell` → `wait_timeout` at N=131071 → force STATE_INIT
  + clear `position_in_0<=ROW_X_COL_X` + LOUD `mixer_resync_cnt`), pristine ungated + fork clk_en-gated;
  diff-shape verified (copies differ ONLY by clk_en), fork lints clean, sim builds clean.
- **Inertness gate FAILED:** not byte-identical on baseline (`+ddr_rd_latency=30`, resync=8) NOR on the
  zero-latency clean path (`+ddr_zero_latency`, resync=8). Fires ~2.0×/frame at zero latency, 2.8×/frame
  harsh — i.e. it fires on NORMAL frame sync, not a distinct pathology.
- **Why the plan is wrong (mechanism-level):** STATE_WAIT exits on `h_pos==0 && display_first_pixel`.
  For a **ROW_X_COL_0** code that matches at the next line (≤1-line wait — the plan's assumption, correct
  only here). For **ROW_0_COL_0 / ROW_1_COL_0** (field-top line-starts) it needs `v_pos==0 / v_pos==1` =
  the raster reaching the top of the field, recurring ~once/frame ⇒ a legit wait up to ~1.77 fields every
  frame boundary. Empirically every dwell >50k carried pos0=0 or pos0=1; none pos0=2.
- **Passive max-dwell measurement (fix disabled, N unreachable, 32-bit counter + tb MAXDWELL latch):**
  peak continuous STATE_WAIT dwell clean(zero-lat)=**401,111** vs harsh=**401,095** dot_clks — identical
  (one NTSC field ≈ 225,917). ⇒ NO N is inert-on-clean yet fires-on-harsh. Mechanism unsalvageable.
- **Reverted** all 3 touched files (both mixer.v + tb_memshim.v) to committed; reverted tree rebuilds
  **byte-identical** to the pre-fix reference (sanity-confirmed). No broken RTL shipped.

## Corrected mental model (supersedes prior handoff's "RESIDUAL: mixer fragility")
The prior handoff called the harsh sluggishness "mixer underrun-recovery fragility" and proposed the
bounded-STATE_WAIT re-sync. That framing is now **retired**: under the NTSC-matched modeline there is no
mixer freeze — the long STATE_WAIT parks are the mixer's *normal, correct* frame-boundary raster sync.
Under harsh latency the DECODER is briefly starved (a memory-throughput/pacing effect); the mixer then
waits (correctly). A mixer-FSM change is the wrong layer and can only corrupt output. Observed once under
harsh: a momentary decode starvation (`[traj] i` frozen for one heartbeat) that self-recovers — benign.

## Next steps (ranked)
1. **The REAL HW scanout symptom** (black display / OSD-squish) — the SEPARATE video-timing / interlace
   bug, [[scanout-blind-spot-ddr-vs-crt]]. Decode-independent, NOT the (retired) mixer story. Triangulate
   via the Frank-menu test + an HDMI OSD grab before touching syncgen interlace.
2. **Audio / A-V sync / PS→ES demux** — real DVD VOBs are program streams; current ingest is ES.
3. **(OPTIONAL, only if a smoothness problem is ever OBSERVED on HW)** characterize decode throughput vs
   memory latency at the **memory/pacing layer** (NOT the mixer). This is a "does the decoder get data
   fast enough under real DVD bitrate + f2sdram latency" question. Do not touch the bus without evidence
   of a real HW symptom; the sim shows decode always advances (no watchdog) even under harsh latency.

## Reproduce / validate (sim-only, from core/sim/memshim; each iter a few min, no FPGA build)
- Falsification (fix was here, now reverted): the fix fired 8× on `run_memshim.sh run 4 300 --
  +ddr_zero_latency` (should be 0 for a valid inert fix). To re-measure the true dwell if ever needed:
  re-apply a passive `wait_dwell` counter + a tb `max_wait_dwell` latch with N unreachable, run zero vs
  harsh, compare peaks (they match at ~401k → no viable timeout).
- Healthy baseline (current committed tree): `make build && ./run_memshim.sh run 4 300 --
  +ddr_rd_latency=30` → 4 frames, no stall. `make build` defaults NTSC (keep it matched to the clip).
- Byte-identical anchor: whole-file md5 of framestore_0000..0002 + tv_out_0000..0012 (drop the LAST of
  each series — the sim is killed mid-write so the last ppm is partial/non-deterministic).

## Landmarks
- `docs/plans/mixer-hardening-bounded-statewait-resync.md` — ⛔ FALSIFIED banner + postmortem at top.
- `core/mpeg2fpga/rtl/mpeg2/mixer.v:109-114` — `first_pixel_read` / `display_first_pixel` (the
  ROW_0/1_COL_0 field-top-wait vs ROW_X_COL_0 next-line-wait distinction the plan missed). `:122`
  STATE_WAIT arm. `syncgen.v` v_pos parity (`v_pos LSB = ~odd_field`). Fork copy identical + clk_en.
- `core/sim/memshim/Makefile:37` — `MODELINE ?= MODELINE_NTSC_INTERL` (harness fix from #10; keep matched).
- `docs/progress.md` #11 — full trail with numbers. #10 — the PAL-modeline root-cause it builds on.
- Memory: [[mixer-hardening-falsified-field-top-wait]], [[display-wedge-pal-modeline-artifact]].

## Open questions / risks
- Is there ANY observed HW smoothness problem to justify pursuing the throughput residual? None seen yet
  (decode advances on HW; the sim never watchdogs under harsh). Treat as "no bug until a HW symptom".
- The sim's dot_clk free-runs; HW paces the mixer via `dot_ce`. Underrun *dynamics* differ slightly, but
  the falsification is structural (field-top waits exist on any correct raster) and holds regardless.
- Keep the sim MODELINE matched to the clip (NTSC) — never re-introduce the PAL mismatch (#10).
