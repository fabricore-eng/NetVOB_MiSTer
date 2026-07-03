# Handoff — dvd — 2026-07-03
Branch: feat-decoder-bringup   ·   Repo: ~/Dev/fabricore/NetVOB_MiSTer

## TL;DR
The weeks-long decoder stall is ROOT-CAUSED and FIXED in sim: the mem_shim command
handshake silently dropped commands whenever a hold coincided with a delivered word
(pulse-valid FIFO semantics) — the same disease that runs on the board today. Shim
rebuilt as a loss-proof 4-deep credit queue (+ the RAW staleness guard). The first
dell build died on a trivial SystemVerilog reserved-keyword typo (`dist`); that's fixed,
re-validated byte-identical, re-staged, and **rebuilding on dell now**. Next: confirm the
build passes, then run the HW decode gate (needs the human's per-run go) — target is the
frame-0 SSIM≥0.95 decode-correctness milestone.

## State of play
- **Done (verified this session):**
  - Wedge milestone stands (2/2 HW boots, prior session): SDC clock-groups fix killed the
    f2sdram wedge; slice 1 decodes near-bit-perfect on silicon (0.987).
  - Full stall forensics — mapped the freeze chain link by link (docs/HANDOFF-2026-07-02-
    stall-forensics.md). Proximate cause = shim LOST WORDS: the request FIFO is pulse-valid,
    so any branch that examined a delivered word without consuming/capturing it LOST it (27
    losses/run → tag-without-response → response-router wedge → total decode freeze). Every
    historical direct-path hold (write-gate, throttle, RAW guard) carried this — including on HW.
  - FIX: mem_shim command path rebuilt as a 4-deep CREDIT-BASED queue (words always land,
    credit-gated rd_en guarantees capacity, FSM processes only the head, holds = don't-consume;
    full 1-pull/cycle rate; a `lost_words` counter that MUST stay 0).
  - Sim-validated byte-identical: no-op equivalence (frame0 slot0 = f089ee06), staleness
    immunity (+ddr_wr_commit_delay=32 now DECODES with RAW-STALE=0, was instant death),
    LOST_AT_SHIM=0 everywhere.
  - Build-blocker fixed: `dist` is a SystemVerilog reserved keyword (Quartus Error 10170); the
    sim parser tolerated it, Quartus (this file is .sv) rejected it. Renamed → `ring_dist`,
    re-validated byte-identical (frame0 = f089ee06, LOST=0 at wp0 AND raw32). Shim md5 now
    **3d8e036d**.
- **In progress:**
  - dell Quartus build of shim 3d8e036d running (relaunched ~2026-07-03; detached,
    dell:/tmp/dellbuild-dvd.log). A background poll (task) is confirming A&S clears the old
    18s death point.
- **Blocked:** nothing right now. The HW decode gate needs the human's explicit per-run "go".

## Key decisions (and why)
- **Credit queue, not more skid holds.** The lost-word disease is inherent to "examine a
  pulse-valid word without housing it." A queue where every delivered word ALWAYS lands and
  holds simply don't-consume-the-head is loss-free by construction — verified by a permanent
  word-conservation audit (LOST_AT_SHIM must be 0). Don't reintroduce direct-path holds.
- **RAW guard retained (RAW_GUARD_AGE=128).** The f2sdram posted-write staleness hazard is
  real and separately proven (ring-distance + age test, no CAM). It rides inside the credit-
  queue shim. `dist`→`ring_dist` was ONLY a keyword rename; behaviour unchanged.
- **Display-ack wedge is PARKED (scanout rung, NOT decode-blocking).** Under any timing shift
  the raster-side disp consumer stops draining → resample_addrgen stuck STATE_WAIT →
  output_frame_valid never acked → picbuf→motcomp→vld freeze at ~frame 4. The decode gate reads
  SETTLED framestore content; frames 0-2 settle byte-identical BEFORE this wedge, so the SSIM
  milestone is reachable without fixing it. Fix design is in the forensics handoff §FINAL.
- **Shim ships as a patch, built via the hub launcher.** core/MiSTer_MPEG2 is a non-pushable
  submodule; the shim change lives in core/patches/hw/mpeg2fpga-memshim-credit-queue.patch and
  is scp'd to dell's working tree for the no-ref hub build.
- **Whole-ppm md5s are INVALID under timing perturbation** (dump-instant skew of in-progress
  slots). Compare per-slot Y via tools/build/extract_framestore_slots.py, or content-vs-ref SSIM.

## Next steps
1. **Check the build result:** `~/Dev/fabricore/tools/tools/dell_build.sh --who` → expect a
   fresh `dvd DONE rc=0`. If rc≠0: `ssh dell "grep -E 'Error \(10' /tmp/dellbuild-dvd.log | head"`
   (watch for ANOTHER SV-reserved-word snag, though a scan found `dist` was the only one).
   The armed poll task will also report "A&S CLEARED" (fix took) or the failure.
2. **Sanity-check timing:** in the build's STA, worst-case SETUP was +0.040ns before the queue;
   the queue adds logic. If setup went negative, timing-iterate BEFORE the gate (don't gate a
   failing-timing build).
3. **HW decode gate (NEEDS THE HUMAN'S GO):**
   `~/Dev/fabricore/NetVOB_MiSTer/tools/build/hw_flash_and_gate.sh crediq`
   (devlock-gated, warm-reboot, flash, .mgl load, UART, framestore dump, SSIM). Expect: W climbs
   far past 2.29M (decode continues past the old slice-2 death), framestore frames 0-2 settled,
   **frame-0 SSIM vs ref_frame_01 ≥ 0.95** = the decode-correctness milestone.
   Verify with: gate.txt VERDICT + the UART word (U:0, RP tracks P) + hw_gate_runs artifacts.
4. **(Later rung) Display-ack wedge:** underrun-robust vsync resync of the disp addr/data
   pairing; re-validate with the word audit + trajectory trace. Only needed for scanout.

Verify step 1 done with: `dell_build.sh --who | tail -1` shows `dvd DONE rc=0 <sha>`.

## Landmarks
- `core/MiSTer_MPEG2/rtl/mem_shim.sv` — the credit-queue shim (md5 3d8e036d). Queue at the
  `q_cmd/q_addr/q_dta` decls; `raw_collides()` (now `ring_dist`) ~line 213; credit pull policy
  at the bottom of the command-path always-block.
- `core/patches/hw/mpeg2fpga-memshim-credit-queue.patch` — the shim as a re-appliable patch
  (vs submodule HEAD); this is what git tracks.
- `core/sim/memshim/tb_memshim.v` — word-conservation audit + trajectory trace + stall-report
  probes (vbuf/getbits/vld-gate/fwd-reader/recon/picbuf/resample). Word audit = the gate.
- `docs/HANDOFF-2026-07-02-stall-forensics.md` — the full forensic chain + §FINAL (display wedge
  fix direction + staging checklist). READ THIS for the deep context.
- `docs/memshim-raw-guard-design.md` — RAW-guard design + validation ladder.
- `tools/build/extract_framestore_slots.py` — per-slot Y extraction (timing-robust comparator).
- `core/sim/memshim/run_stall_grid.sh` — the oracle experiment arms.

## Open questions / risks
- Does Quartus hit ANOTHER SV-reserved-word identifier after `dist`? (Scanned the shim — clean —
  but the A&S-clear poll is the real confirmation.)
- Does the credit queue meet SETUP timing? +0.040ns was already tight; the queue adds logic.
- On HW, does the fix carry the framestore to settled frames 0-2 and frame-0 SSIM≥0.95? (Sim says
  yes; the age-gate write-gate hold — the suspected HW stall — is now gone.)
- Meta-lesson: this 2007 design is full of pulse/count-aligned handshakes that never resync (3
  instances found in one session). Treat EVERY new hold/delay as suspect; run the word audit +
  trajectory trace + per-slot extractor on any change.
- Ops: monthly spend limit was hit mid-session (no subagents/workflows — solo only); may have
  reset. Peers 573 + stream wound down; de10 free, no locks held. `dist`-class bug = a good
  hub LESSONS.md candidate (sim parser lenience ≠ Quartus; .sv files hit SV reserved words).
