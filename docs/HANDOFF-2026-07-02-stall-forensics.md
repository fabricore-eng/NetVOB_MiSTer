# HANDOFF 2026-07-02 — decoder-stall forensics: the freeze is a decoder-internal
# flow-control wedge, latency-perturbation-triggered; localization nearly complete

**Supersedes the "RAW staleness is THE mechanism" framing in HANDOFF-2026-07-01-sdc-clockgroups-fix.md's
decision tree.** Read with docs/progress.md (bottom ~6) and tools/hw_gate_runs/*/ANALYSIS.md.

## Where we are (chain of established facts, each sim-proven)
1. **Wedge milestone stands** (2/2 HW boots): the SDC clock-groups fix killed the f2sdram wedge;
   slice 1 decodes near-bit-perfect on silicon (0.987). HW then stalls at a boot-varying point.
2. **RAW staleness is REAL but may be secondary**: the f2sdram posted-write hazard exists (model
   +ddr_wr_commit_delay; ≥32-cycle windows kill decode via VLD desync; ≤8 safe). A shim-level RAW
   guard was built (ring-distance+age, no CAM, mem_shim.sv; the direct-path-hold LOST-WORD trap was
   found+fixed — any hold MUST skid-capture, see the RTL comment). Guard is mechanically sound
   (inert-guard bisect = byte-identical baseline).
3. **THE HEADLINE: a decoder-internal pipeline freeze reachable by ANY read-latency perturbation
   with fully CORRECT data.** Guarded knobs-off run (correct data, in-order, bounded delays ≤128cyc)
   freezes at mb=453 frame 2(B) — the same place the stale-data run freezes (mb=456) at the same
   read count (~33.6K). Baselines (no perturbation): healthy (greyramp control decoded 23 frames to
   the cycle cap; testsrc2 baseline 5 frames clean, I/P/B all fine).
4. **Trajectory trace** (run_g7_traj): the READ side freezes first (rd/rdcnt/mb static from 44ms);
   stream ingest continues until the vbuf ring legally wraps+fills. Ring accounting exonerated.
5. **Freeze state** (run_g8_gates): vld_en=0 because **mvec_wr_almost_full=1**; motcomp_busy=0;
   **fwd_rd_addr NOT empty** (reference-read addresses pending) but **fwd_wr_dta_almost_full=1**
   blocks do_fwd; recon produces nothing (recon_rd_empty=1). The fwd DATA fifo holds 64+ words that
   motcomp_recon never consumes: recon sits in STATE_WAIT gated by row-valids
   (motcomp_recon.v:189-201) — the fwft2_reader fall-through adapters feed those.
6. **framestore.v:89**: the response router STOPS DRAINING ALL responses when ANY client dta fifo
   is FULL — the loop-closer candidate for the total read freeze.
7. **Build-config landmine**: sim default FIFO_GRAY=0 = proven OpenCores generic_fifo_dc; the HW
   build uses the FORK's hand-written Gray xilinx_fifo_dc (core/sim/memshim/Makefile:43-53 calls
   swapping "the decisive experiment"). The freeze reproduces even with the PROVEN fifo ⇒ the defect
   is in the flow-control chain itself; the fork fifo on HW may be an ADDITIONAL failure mode.
8. Killed theories (each with evidence): ADDR_ERR synthetic-response overtake (0 occurrences),
   response-FIFO overflow (arithmetically covered), ring-pointer corruption (post-wrap misread),
   fifo depth constants (log2 units, upstream-consistent), random bit corruption as the HW mechanism
   (VLD recovers from it — cwr2), motcomp clk_en gating (hardwired 1).
9. In-flight when this doc was written: run_g9_final probes {fwd_reader fifo full/valid both sides,
   recon row-valid gates, response-router empties} at the freeze — expected to pin the exact broken
   link: fwd dta fifo FULL blocking the router (6) vs fwft2 handshake wedge vs dc-fifo occupancy
   desync under response clumping.

## Unifying picture
Baseline-smooth response timing never lets the fwd/vbr dta fifos build; ANY perturbation (guard
holds, stale-data-desync request patterns, and on HW: real DDR latency/refresh clumping) piles
responses into a client fifo mid-macroblock until a full/afull condition closes a circular wait
(VLD ⟂ mvec ⟂ motcomp ⟂ fwd-reads ⟂ response-router). Boot-to-boot HW variance = when the first
fatal clump lands. The HW I-frame door is the same class via the vbr/idct path.

## Next actions (in order)
1. Read run_g9_final's probe lines → name the broken link.
2. Design the minimal fix at that link. Candidates by link:
   - router-blocks-on-full → drain-to-available-clients (never block cross-client) or deepen the
     victim fifo / lower its afull so REQUEST gating (framestore_request) always prevents FULL.
   - fwft2/dc-fifo occupancy desync → fix the adapter/fifo (and re-test FIFO_GRAY=1!).
   - threshold race (afull mid-MB) → set DTA thresholds to reserve a full MB's beats
     (fifo_size.v change; sim-validate).
3. Validate: both clips, wp=0/2 per-slot identical (use tools/build/extract_framestore_slots.py —
   whole-ppm md5s are INVALID under timing perturbation); then guard-on runs; then raw32/comp arms
   must DECODE (fix + guard together = immunity); then FIFO_GRAY=1 sweep (the HW fifo!).
4. dell staging: mem_shim.sv (RAW guard, md5 after final = see git) + any mpeg2fpga fixes as patch
   files (submodule non-pushable, per dell-build-mechanics); build via hub launcher; HW gate needs
   the human's per-run go.

## Session ops notes
- Monthly spend limit HIT: no subagents/workflows until raised — solo probes only.
- Chat watcher must be re-armed every turn; 573 closed their session (de10 free, their milestone
  landed); stream false-alarm closed (mostly-black BOOT CHECK is legit output).
- Sim probes live in tb_memshim.v (trajectory trace + stall-report extensions) — committed; the
  RAW-guard mem_shim.sv is the working-tree version, NOT yet staged to dell.

---
## FINAL STATE (end of session, forensics COMPLETE)

**The freeze chain, fully mapped (every link probe-verified):**
1. ~~Shim lost words~~ **FIXED**: pulse-valid FIFO semantics meant examine-without-consume LOSES the
   word; all direct-path holds had it (27 losses/run → tag-without-response → router wedge → the
   frame-2 freeze). Fix = 4-deep credit-based command queue (patch
   `core/patches/hw/mpeg2fpga-memshim-credit-queue.patch`, shim md5 280e0cb9 incl. RAW guard@128).
   Gate criterion forever: the tb word audit's LOST_AT_SHIM==0 and the shim's lost_words==0.
2. **REMAINING (scanout rung, NOT decode-blocking)**: the display-ack wedge. Under ANY command-timing
   shift (guard holds, credit-queue latency, HW latency — guard-inert discriminator proved it's not
   the guard): raster-side disp-data consumer stops draining → disp dta fifo full → do_disp blocked →
   disp ADDR fifo afull → resample_addrgen stuck in STATE_WAIT (probe: state=4, disp_addr_afull=1) →
   never re-enters STATE_INIT → never acks output_frame_valid (probe: out_valid STUCK 1, out_rd 0) →
   picbuf_busy → motcomp_busy → vld_en=0 at the NEXT picture boundary (frame 4). The raster itself
   stays alive (tv_out keeps producing) — it's the frame handshake that starves. Matches the HW
   post-stall signature (black display, slow frame-window reads). FIX DIRECTION (next session):
   underrun-robust resync of the disp addr/data pairing at vsync (flush+realign), or make picbuf's
   rotation not block decode when the display is wedged (decouple with care — never mask: log it).
   The final micro-root (the raster consumer's exact stuck condition in resample dta/bilinear) is one
   probe deeper via `mpeg2.resample.*` if needed.
3. Meta-lesson (3 instances in one session): this 2007 design is riddled with pulse/count-aligned
   handshakes that never resync — safe on deterministic 2007-Xilinx memory, fragile on any
   perturbation. Treat EVERY new hold/delay as suspect against this class; the word audit + the
   trajectory trace + per-slot extraction are the standing verification kit.

**Why the decode milestone is NOT blocked**: the decode gate reads SETTLED DDR framestore content.
With the credit queue + guard, sim decodes frames 0-2 byte-identical-to-baseline before the frame-4
display wedge; on HW the same should carry the framestore past the old slice-2 death to settled
frames 0-2 → the SSIM>=0.95 decode-correctness gate on frame 0 is in reach THIS build.

**Staging checklist (next actions)**:
1. Confirm run_v_final_base (settled slots f089ee06/b64eca3d, LOST=0) + run_v_final_raw32 (decode
   SURVIVES 32-cycle staleness, RAW-STALE=0-or-few, LOST=0).
2. scp rtl/mem_shim.sv (md5 280e0cb9) to dell (working-tree build per dell-build-mechanics; qsf
   stays clean; holdfix SDC already has the clock-groups fix).
3. Hub-launcher build (DELL_PROJECT=dvd DELL_TARGET=mpeg2fpga DELL_REPO=NetVOB_MiSTer/core/MiSTer_MPEG2,
   no ref). ~35 min. Check STA: worst setup was +0.040ns — the queue adds logic; if setup goes
   negative, timing-iterate before the gate.
4. hw_flash_and_gate.sh (human's per-run go). Expect: W far past 2.29M (decode continues past old
   death), framestore frames 0-2 settled, frame-0 SSIM vs ref_frame_01 — the 0.95 gate.
