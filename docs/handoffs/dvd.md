# Handoff — dvd — 2026-07-15 (latest)
Branch: feat-decoder-bringup   ·   Repo: ~/Dev/fabricore/NetVOB_MiSTer

## TL;DR
**Implemented + SIM-VALIDATED the CRT black-video fix: DISPLAY READ BURSTING in `mem_shim.sv`.** The scanout
black was display-read starvation under f2sdram latency (root-caused 2026-07-03). The lever, quantified last
session, is words-per-outstanding-transaction — i.e. **bursting**, the only way to beat the throughput ceiling
inside the ≤5-outstanding HW read cap. Built a burst prefetch engine in the shim; in the memshim sim oracle at
`+ddr_rd_latency=120` the tv_out peak field mean **recovers 13.5 → 74.9 (= the healthy lat30 baseline of 74.7),
a full recovery**, with decode bit-exact vs baseline and no regression at lat30. A 5-lens adversarial review
found **2 real bugs in the recovery (`resp_timeout`) path** — both fixed and re-validated. **NOT yet built on
dell.** The #1 remaining risk is TIMING/placement (see Watch out). RTL changes are committed to the
`MiSTer_MPEG2` submodule; sim harness + patch committed to `NetVOB_MiSTer`.

## What the fix is (mem_shim.sv burst prefetch engine)
Reads BELOW the vbuf window (frame stores + OSD, `addr < 22'h1C0000`) are issued to DDR3 as **`ddr3_burstcnt=8`
Avalon bursts** instead of single words. Beat 0 answers the requesting read; all 8 beats land in one of **8
fully-associative prefetch buffers**. Later reads are served three ways:
- **HIT** — address falls in a COMPLETED (VALID) buffer → answered from the buffer, **no DDR transaction**.
- **FORWARD** — address falls in a buffer whose burst is still IN FLIGHT → waits for that exact beat (no
  duplicate burst issued).
- **MISS** — issue a fresh burst, evicting a buffer (INVALID → cold-VALID → any; hot-bit protects live display
  line buffers from motcomp-miss churn, CLOCK-style).

The display's six interleaved sequential streams (OSD/Y pairs + U-upper/lower + V-upper/lower, per
`resample_addrgen.v`) then cost **~1 transaction per macroblock instead of 8**; motcomp row pairs cost 1 instead
of 2-3. Measured hit+fwd rate at lat120 ≈ **80%** of display words served without a DDR round-trip.

**The contract it preserves** (from a 5-agent contract audit of `framestore_response.v` / the FIFOs / `emu.sv`):
`framestore_response` pops one tag + one response word in **positional lockstep** — every read must produce
**exactly one** `mem_res_wr` word in **exact global request order**, hits and misses alike; one lost/dup/reordered
word = permanent silent decode wedge. So ALL responses — DDR beat-0s, hits, forwards, ADDR_ERR synthetics,
timeout recoveries — funnel through **one 16-deep in-order response queue (`rq`)** that is the SOLE driver of
`mem_res_wr_*`, allocated at request time, drained strictly head-first. (This also fixed a latent pre-existing
hazard: the old direct ADDR_ERR synthetic path could emit ahead of an outstanding earlier read.)

Key invariants (all from the audit, all honored):
- Outstanding **transactions** stay ≤ `READ_LIMIT` (raised 4→**5**; HW bridge locks at 6). Beat-vs-transaction
  counting reworked: `outstanding`/write-gate/`resp_timeout` all count transactions (decrement on last beat).
- Writes ALWAYS issue `burstcnt=1` (`ddr3_burstcnt` is shared read/write; the f2sdram safe-terminator tracks
  write bursts and would wedge on a multi-beat write).
- Bursts are **clamped at the VBUF boundary** (`blen = min(8, VBUF-addr)`) so no burst tail spills into the
  RAW-guarded vbuf ring. vbuf reads stay single-word on the exact pre-existing RAW-guarded path.
- An accepted WRITE **invalidates** any buffer whose fetched range covers it (VALID→INVALID; in-flight→killed at
  completion), computed combinationally so a same-cycle write-vs-completion resolves correctly.
- Response-fifo overflow-safe: every `rq` entry admitted only while `!mem_res_wr_almost_full` (asserts at ≥64
  used of 128 → 64 free); committed words bounded by `RQ_DEPTH=16 ≪ 64`.
- Also raised `MEMTAG_THRESHOLD` 16→**8** (`fifo_size.v`): the 0a diagnostic showed the display starves through
  the **tag** almost-full gate (tag_af duty 45-78% under HW latency), not the address-gen or data gate. Reserving
  8 free tag slots (of 32) instead of 16 stops that gate throttling the display; slip is ≤2-3 requests (registered
  flag) so the tag fifo still can't overflow.

## 0a diagnostic (did FIRST, as the handoff said) — result
Added `disp_rd_addr_empty` + do_disp gate-component duty counters to the tb `[gate]` line, reran lat30 vs lat120.
Verdict: do_disp is **throttle-bound, not address-gen-bound** — `disp_rd_addr_empty` duty was identical at lat30
and lat120 (address gen keeps up), while `tag_wr_almost_full` duty rose from ~45% (lat30) to **~78% (lat120)**.
So the display loses at the shared request/tag gate under latency → confirmed a THROUGHPUT fix (bursting +
tag-threshold), not a deeper disp-addr prefetch. Baselines reproduced exactly: lat30 peak **74.7**, lat120 **13.5**.

## Sim validation (memshim oracle, `core/sim/memshim`, NO FPGA build)
| run | peak field mean | notes |
|---|---|---|
| lat30 baseline (pre-fix) | 74.7 | healthy reference |
| lat120 baseline (pre-fix) | **13.5** | the black-video repro |
| **lat120 + fix** | **74.9** | **full recovery** (= baseline) |
| lat30 + fix | 74.5 | no regression; **decode frames bit-exact** vs baseline (content-hash match) |
| lat240 + fix | 54 (peak) / 42 mean | was 9.2 (≈black); large improvement under 2× HW latency |
| lat120 + jitter (±60) | 76.7 | 0 stalls under variable latency |
| lat120 + ADDR_ERR inject (period 997) | 74.6 | synthetic-response path clean, 0 stalls |
| lat120 soak (8 frames) | 74.9 | 46 fields, 0 stalls/BUG, cache steady |

Decode is untouched by design (no deadline) and stays bit-exact; the fix only changes WHERE/WHEN display words
arrive, never how many or in what order. Metric tool: `core/sim/memshim/tv_metric.py` (per-field luma mean;
content fields reach ~74 healthy, <15 starved).

## Adversarial review (5 lenses) → 2 bugs found + FIXED + re-validated
All findings clustered in the **`resp_timeout` recovery path** (fires only when the bridge drops responses — the
historical HW wedge). No default sim exercises it, so these were exactly the rare-path bugs the review targeted.
1. **CRITICAL (confirmed by full verify, found by 3 lenses):** a FORWARD allocated on the *exact cycle*
   `resp_timeout` retires its target head transaction was orphaned on a dead tx slot → permanent drain wedge (or
   wrong-data on tx-slot wrap). The retire's forward-synthesis loop reads pre-edge `rq_state` and can't see the
   NBA-allocated forward; `pfm_pend` had no `resp_timeout` term (the existing `+rdv_q` guard is 0 on a timeout
   cycle). **Fix:** exclude the head buffer from `pfm_pend` when `resp_timeout` (mem_shim.sv:244-247) → the read
   re-fetches (MISS) or holds one cycle instead. Mirrors the existing beat-completion guard.
2. **minor→real (found by 4 lenses):** timeout beat-0 synthesis keyed on `rq_state[tx_rq[tx_head]]==RQ_MISS`, but
   after a partial-burst loss (beat 0 arrived, entry drained + recycled through the 16-wrap) that stale rq index
   can alias a live read's entry and zero it → count desync. **Fix:** added a per-transaction `tx_b0done` bit; the
   timeout synthesizes the requester only when `!tx_b0done[tx_head]` (unambiguous, no stale deref).

**Adjudicated NOT-fixed (documented residuals, argued against the code):**
- *Soft-reset with beats in flight* — the decoder does ~491k STATE_CLEAR write cycles before its first read and
  stale beats drain in <1024 cycles with `tx_count==0`, so the stray-beat guard drops them; the new guard is
  strictly better than the old shim's unconditional `mem_res_wr_en<=rdv_q`. Not a regression.
- *Below-VBUF posted-write RAW cached* — pre-existing hazard class (below-VBUF RAW was never guarded); recon-write
  vs display-read rarely hit the same address (different frame buffers); quality not correctness. Widened modestly
  by caching; documented, not fixed (a proper fix needs a below-VBUF RAW guard, a bigger change).
- *Timing / M10K extraction* — build-time observables, see Watch out.

**Recovery-path fixes re-validated in sim (new DDR-model injection knobs):**
- `+ddr_tail_drop_period=N` (drop a burst's last beat) — 8 partial losses at lat120: **0 stalls, 0 BUG/LOST, 4
  frames decoded, display 74.8**. (Proves partial loss doesn't wedge/desync; count preserved via the rq.)
- `+ddr_outage_at/_len` (full recoverable beat outage — the ONLY way to actually FIRE `resp_timeout`; a
  30k-cycle beat hold during active display, `+ddr_outage_at=3500000 +ddr_outage_len=30000`): **`resp_timeout`
  fired 6× (`recov=6`), `wedged=0`, 0 stalls/BUG, decode resumed and reached 4 frames.** The recovery path is
  exercised and the fixed engine recovers cleanly — no permanent wedge, no count desync. (An earlier outage at
  cycle 2M fired 0 recoveries because it landed in frame-1 decode before display bursting began — no burst txns
  were outstanding to time out; retriggered during active display.)

## Landmarks (file:line, post-fix)
- `core/MiSTer_MPEG2/rtl/mem_shim.sv` — the whole burst engine. Header block ~:115. Buffers/rq/tx decls ~:180-210.
  `pfm_valid`/`pfm_pend` hit logic + eviction :236-283. Reset :440-475. Response drain :484-500. Beat router
  :517-551. `resp_timeout` retire :552-577. S_IDLE decision (HIT/FWD/MISS/vbuf/ADDR_ERR) :605-720.
- `core/MiSTer_MPEG2/rtl/mpeg2/fifo_size.v:206` — `MEMTAG_THRESHOLD=9'd8` (also mirrored to sim via the
  `core/patches/mpeg2fpga-memtag-threshold.patch` + the incdir symlink; and in `core/mpeg2fpga` working tree).
- `core/sim/memshim/ddr3_model.v` — burst-aware response pipeline (seq-ordered drain, `rsp_last` = txn boundary)
  + injection knobs `ddr_tail_drop_*` and `ddr_outage_*`.
- `core/sim/memshim/tb_memshim.v` — `[gate]` (0a duty counters), `[cache]` (hit/fwd/miss/recov/wedged).
- `core/sim/memshim/tv_metric.py` — the per-field brightness metric (stdlib, no PIL).

## Next steps (ranked)
0. **BUILD on dell and READ THE TIMING REPORT** (`fabricore:dell-build`; DELL_PROJECT=dvd DELL_TARGET=mpeg2fpga
   DELL_REPO=NetVOB_MiSTer/core/MiSTer_MPEG2). The engine adds a wide S_IDLE combinational decision cone (8× 22-bit
   subtract/compare + priority encoders + raw_collides → consumed_a/rd_en) at 108 MHz on a placement-marginal core
   — timing closure is the real risk, NOT function. If Fmax fails, pipeline the hit-detection: register
   `saved_addr`-derived `pf_off`/hit vectors one stage ahead of the FSM decision (the request backlog is deep, so
   +1 latency is free). Check f2sdram bridge placement didn't re-roll (re-verify the decode gate on HW).
1. **On HW: re-run the bridge lock-probe with bursts.** READ_LIMIT=5 was characterized at burstcnt=1; whether the
   bridge throttles on TRANSACTIONS or BEATS under bursts is an OPEN question (4-5 × 8-beat = 32-40 beats in
   flight). If it counts beats, drop `READ_LIMIT` or `BURST_N`. The FPGA side always drains read data
   (rd_ready tied 1) so beats shouldn't pool, but verify.
2. **Warm-reboot the board, capture, `tools/verify_frame.sh`** — objective PASS/FAIL vs a reference, not a vision
   read. Expect the CRT video area to go from flat black → picture.
3. After the black is fixed: the minor OSD color tint (component/YPbPr level), then audio / A-V sync.

## Board / setup notes
- SuperStation (mister) is on `direct_video=1` (component → CRT, `vga_scaler=0`); core owns 100% of timing. Keep it.
- The `MiSTer_MPEG2` submodule working tree still carries **prior-session uncommitted HW changes** (emu.sv,
  modeline.v, mpeg2fpga.qsf, holdfix.sdc, rld.v, uart_debug.sv, audio_out.v) — that is the deployed-core state, NOT
  mine; I committed ONLY mem_shim.sv + fifo_size.v. Don't sweep those into a burst-fix commit.
- Memories: [[scanout-black-display-read-starvation]] (root cause), [[dvd-display-read-bursting-fix]] (this fix).

## Watch out
- **READ_LIMIT must stay <6 on HW** (bridge locks at 6). Now 5. Never ship the sim-only ceiling test values.
- **TIMING is the #1 build risk** — placement-marginal core + a new wide decision cone. Sim proves function, not
  Fmax. Read the timing report; be ready to pipeline hit-detection. Prior rushed fixes to this core backfired 3×;
  keep changes minimal, re-verify the decode gate after the build.
- **Burst bridge behavior is HW-unverified:** burstcnt=8 legality + the outstanding-transaction-vs-beat lock
  question (step 1). The framework itself ships 16/128-beat bursts on sibling f2sdram ports, so burstcnt=8 is
  representable — but not proof for f2h_sdram1 under this traffic.
- Do NOT re-attempt arbiter reprioritization (sim-proven inert 2026-07-02) or HALFLINE/mixer-resync (ruled out).
- Keep the sim MODELINE matched to the clip (NTSC), never PAL.
