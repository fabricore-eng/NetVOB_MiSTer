# mem_shim ↔ f2sdram design-space analysis (2026-06-25)

Definitive map of the mem_shim / HPS-f2sdram bridge problem after the Option-A + isolation HW
campaign. Purpose: stop re-exploring dead ends. Pairs with
[`docs/HANDOFF-2026-06-25-bridge-pacing-and-reissue.md`](HANDOFF-2026-06-25-bridge-pacing-and-reissue.md)
and memory `bridge-placement-marginal-root-cause`.

## The decoder↔memory contract (verified in mpeg2fpga RTL — this constrains every fix)

- mem_shim sits between the decoder's **request FIFO** (reads/writes) and **response FIFO**, translating
  to the MiSTer `DDRAM_*` Avalon-MM port (HPS f2sdram bridge).
- **Responses are matched to requests STRICTLY BY POSITION, not by a tag in the response.**
  `framestore_request.v` pushes a 3-bit tag (FWD/BWD/DISP/VBUF) into a **separate parallel tag FIFO** for
  every read. `framestore_response.v` pops the tag FIFO and the mem-response FIFO **in lockstep** (one each,
  same cycle) and routes the response data to the FIFO named by the popped tag. There is a built-in
  desync-detect (`framestore_response.v:249`): if the two FIFOs ever differ in count → "tag and mem_res
  unsynchronized."
- **Consequence:** if the bridge drops ONE read response, the response FIFO is one short → every later
  response lands in the wrong slot → total corruption (and the desync check trips). The *only* way to stay
  aligned after a loss is to inject **exactly one** response into the response FIFO.
- **No backpressure on the response direction.** The bridge asserts `DDRAM_DOUT_READY` whenever it wants;
  mem_shim must consume it. mem_shim *can* backpressure its own writes into the decoder's response FIFO
  (`mem_res_wr_almost_full`), but it cannot tell the bridge "hold this response."
- The FSM is **pipelined / multi-outstanding**: issue a command, wait only for *acceptance*
  (`!waitrequest`), then issue the next — responses lag and accumulate. (getbits used this same base FSM.)

## Three failure modes, all observed on silicon

| # | Failure | Evidence | Nature |
|---|---|---|---|
| A | **Over-issue lock** | unthrottled build piled to **9 reads in-flight** → bridge BUSY-stuck (PC:C489) | real, count-driven |
| B | **Dropped-read → write-block wedge** (SignalTap-PROVEN 2026-06-25, CORRECTS the earlier "throttle-hold" guess) | de10 capture: read response DROPPED (reads=20/rdv_q=19), writes accepted ~160clk, then a write refused -> BUSY stuck -> wedged. outstanding only ever =1 (throttle never engaged). PC:A081 (lock_outstanding=1) | a marginal f2sdram read-response DROP, then a write behind the unanswered read |
| C | **Lost-read desync** | reads occasionally dropped (VN−VL≈1; recovery fired) → positional misalign | bridge drops ~0.1% of responses |

- **A** needs an in-flight cap **below ~9**. The old `READ_LIMIT=4` was tuned to a *sim-assumed* lock@6;
  HW shows ~9, so 4 over-throttles (clamps the pipeline lower than necessary).
- **B** is the dominant killer. **SignalTap (de10, 2026-06-25) PROVED it and corrected the earlier guess:** it
  is a **dropped read response**, NOT the throttle and NOT a command gap. In the capture, outstanding_reads
  peaked at only 1 (the cap=4 throttle never engaged), reads ran single-file ~51clk apart, and responses
  drained fine during gaps — until ONE read response was DROPPED by the f2sdram (reads=20, rdv_q=19). Writes
  kept being accepted ~160clk, then a write was refused (BUSY stuck) -> wedge. So the cap=4/cap=6 builds
  wedging at outstanding=1 (PC:A081) was this lost-read mechanism all along — the "throttle read-HOLD" reading
  was wrong (it was an inference; the silicon shows out never reached the cap). Option A cut the drop rate from
  total-dead (raw VL=0) to ~1-5% but didn't eliminate it. The fix is to (a) AVOID the wedge by never issuing a
  write while a read is outstanding + a short re-sync for a genuinely-dropped read, and/or (b) drive the
  residual drop rate to ~0 (more read-return timing margin). It is NOT the throttle and NOT logic-fixable by
  "non-stalling cap" — that was the wrong target.
- **C** corrupts one read's data when "recovered" by zero-fill, or desyncs everything if unrecovered.

## What's been TRIED / RULED OUT (do not repeat)

- **Single-outstanding / `read_pending` serialize hold** — TRIED, **deadlocked on HW** (the f2sdram appears
  to need continuous pipeline motion; recorded in the recovery patch comment). Rules out the simplest cap.
- **Register the command/addr OUTPUT path** (`ram_*`→`d_*`, `mpeg2fpga-memshim-handoff-register.patch`) —
  TRIED, **failed** (didn't fix the wedge; placement re-rolled).
- **LCELL hold-delay chain / set_min_delay brackets / SEED re-rolls** — TRIED, all abandoned (graveyard in
  `mpeg2fpga_holdfix.sdc`). The command/addr hop is auto-constrained by `clk_mem`; no timing lever helped.
- **LogicLock region pinning** — license-BLOCKED on free Quartus 17.0 Lite (regions silently dropped).
- **Option A: register the read-RETURN INPUT path** (`readdatavalid/readdata`→`rdv_q/rdd_q`,
  `mpeg2fpga-memshim-readreturn-register.patch`) — DONE, **partial win**: revived the dead-read-return on HW
  (raw VL 0→84, reproduced) but did NOT fix failure B (the command-accept half). **Kept** (canonical).
- **Zero-fill recovery** (`resp_timeout`→synthesize 64'd0) — keeps the system *running* (count realigns) but
  **corrupts** that read's data (banned doctrine `never-mask-faults-with-fake-data`). It is a liveness net,
  not a fix; acceptable only as a rare-glitch backstop, not for a clean decode.

## What is INFEASIBLE-as-hoped (and why)

- **Simple in-order re-issue of a lost read** — INFEASIBLE without a reorder buffer. Because matching is
  positional and there's no response backpressure: when read N is lost and you re-issue it, responses for
  N+1,N+2,… are already arriving and would fill slots N,N+1,… (shifted). To re-issue *correctly* mem_shim
  must (1) keep an issued-address FIFO, (2) detect the overdue head, (3) re-fetch it, and (4) **buffer the
  post-loss responses in a mem_shim-internal reorder buffer (depth = max in-flight ≈ cap) until the
  re-fetched datum returns, then emit in order.** Bounded and implementable, but real work — and only worth
  it once the bridge stops wedging (B), so it is **not** the next step.

## Recommended path — UPDATED 2026-06-25 after SignalTap proved B = dropped-read → write-block

SUPERSEDES the "non-stalling cap" plan below (that targeted the refuted throttle-hold theory). The wedge is a
**dropped read response** then a **write issued behind the unanswered read**. Lead fixes (sim-first):

0a. **Gate WRITES on `outstanding_reads==0`** — never issue a write while a read is in flight. Then the bridge
    never sees "write behind an unanswered read" -> it cannot enter the wedge. Pair with a **SHORT re-sync
    timeout** (~a few× normal latency, ~200clk, NOT 2^17) that synthesizes the missing response so a genuinely
    dropped read drains -> mem_shim re-syncs and decode continues (a dropped read becomes a 1-read glitch, not
    a black screen). Reads stay single-outstanding (they already do in the bitstream phase); writes wait ~51clk
    for the read to drain — acceptable. Validate in `core/sim/memshim` after adding a drop->write-block wedge
    model (the oracle has `+ddr_drop`; the `+ddr_gap_wedge` I added models the REFUTED theory — replace it).
0b. **Reduce the residual read-response drop to ~0** — more read-return timing margin (Option A took raw VL
    0->84; the last ~1-5% still drops, plausibly a read/write-interleave timing margin near a response).
    Candidates: a 2nd read-return register stage, or hold-margin work on DOUT_READY/DOUT. Confirm via a re-run
    SignalTap (reads==rdv_q => zero drops). 0a is the more robust target (tolerate the drop); 0b is additive.

### (superseded) earlier non-stalling-cap plan — kept for context, do NOT pursue as primary:

1. **Build the gap→wedge into the sim oracle, then design a NON-STALLING cap.** Enhance
   `core/sim/memshim/ddr3_model.v`: if no command is accepted for K cycles while reads are outstanding, WEDGE
   (waitrequest stuck, responses stop) — the "needs pipeline moving" hazard. Confirm it reproduces HW
   (throttle=4 wedges, throttle-off runs). Then design a cap that **never creates a command gap**:
   - **Best candidate: separate read/write issue** so WRITES keep flowing during a read-deferral (writes
     don't count against the read cap and keep the pipeline moving). Needs a small write-bypass/queue with
     read-after-write hazard ordering. The single in-order FIFO is *why* the current hold gaps (a held read
     blocks the writes behind it → idle).
   - Alternative: tune the decoder's own backpressure (`mem_req_almost_full` / response-FIFO depth) so natural
     in-flight stays < 9 WITHOUT a mem_shim hold (replicate getbits's "naturally < 9" without luck).
   Sim-validate: decode byte-identical, in-flight < 9, NO command gap ≥ K, no wedge.
2. **(Optional) SignalTap to confirm the gap→wedge mechanism** before committing a big restructure — capture
   `DDRAM_*` to verify the bridge stops draining responses during a command gap (vs some other cause). The
   inference is strong (reproduced ~84 across 2 placements) but SignalTap removes the last doubt cheaply
   relative to a wrong restructure.
3. **Correct lost-read recovery (failure C)** — implement the reorder-buffer re-issue (above) and DELETE the
   zero-fill, once B is solved and a placement decodes (turns getbits's "partial then desync" into a full
   clean decode). Sim-validate against `+ddr_drop`.
4. **Over-issue lock (A)** is then handled by the non-stalling cap from step 1 (keeps in-flight < ~9 without
   a gap). The old `READ_LIMIT=6` value is sim-validated decode-equivalent but its HOLD method is the B
   wedge — do NOT ship the hold; the cap must be non-stalling.

## Sim oracle now reproduces BOTH failure modes (2026-06-25)

`core/sim/memshim/ddr3_model.v` now models the full HW catch-22:
- `+ddr_lock_threshold=N` — failure **A** (over-issue lock): wedge once N reads are outstanding (HW ≈ 9).
- `+ddr_gap_wedge=K` — failure **B** (NEW): wedge if NO command is accepted for K cycles while reads are in
  flight (the "needs pipeline moving" hazard — what the throttle's read-HOLD trips). Plus `+ddr_drop=N` for
  failure C (lost read).

**Validated against HW** (rd_latency=80): throttle=4 + `gap_wedge=40` → GAP-WEDGE fires at 4 in-flight,
decoder stalls @ macroblock 15, framestore empty — reproduces the cap=4/cap=6 HW wedge. throttle=63 +
`gap_wedge=40` → no wedge but in-flight climbs to 16 → would trip `lock_threshold=9`. So with BOTH knobs on
(`+ddr_gap_wedge=40 +ddr_lock_threshold=9`) the oracle exhibits the exact HW dilemma, **offline in seconds**.

A correct fix must, against `+ddr_gap_wedge=40 +ddr_lock_threshold=9`: decode (frames), keep in-flight < 9,
AND never let cmd_gap reach 40 — i.e. a **non-stalling cap**. Candidate designs to build + validate here next:
1. **Separate read/write issue** (preferred): keep WRITES flowing while a read is deferred, so the command
   pipeline never gaps. Needs a read-after-write hazard guard. The single in-order request FIFO is the root of
   the gap (a held read blocks the writes behind it → idle).
2. **Keep-alive command during a read-deferral**: issue a harmless command (e.g. a WRITE to a reserved scratch
   word) to reset cmd_gap without adding an outstanding read. Simpler, but rests on the HW assumption that
   *any* accepted command (not specifically a read) keeps the bridge draining — verify on HW.
3. **Decoder-backpressure tuning**: size the response FIFO / `mem_req_almost_full` so natural in-flight stays
   < 9 with NO mem_shim hold (replicate getbits's "naturally < 9" deterministically).

Caveat: `gap_wedge` is an *inferred* model (from 3 HW builds); SignalTap can confirm the bridge truly stops
draining during a command gap before committing to a big restructure.
