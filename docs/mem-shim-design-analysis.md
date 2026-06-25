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
| B | **Throttle-hold command-gap wedge** | cap=4 AND cap=6 builds BOTH wedge @~84 reads on a WRITE, BUSY stuck, 1 in-flight (PC:A081); throttle-OFF ran 6000 | the read-HOLD itself (reproduced across 2 placements) |
| C | **Lost-read desync** | reads occasionally dropped (VN−VL≈1; recovery fired) → positional misalign | bridge drops ~0.1% of responses |

- **A** needs an in-flight cap **below ~9**. The old `READ_LIMIT=4` was tuned to a *sim-assumed* lock@6;
  HW shows ~9, so 4 over-throttles (clamps the pipeline lower than necessary).
- **B** is the dominant killer and — UPDATED 2026-06-25 after the cap=6 build — is **the throttle's read-hold,
  NOT a placement lottery.** Two *different* placements (READ_LIMIT=4 and =6) wedge at the *same* point (~84
  reads, on a write, 1 in-flight); the throttle-OFF build (=63) ran 6000. A placement lottery would wedge at
  random points; the consistent ~84 (= when read activity first saturates the cap and the throttle first
  HOLDS) means the **first read-hold gaps the command stream and the f2sdram wedges** ("needs the pipeline
  moving" — now confirmed, not speculative). This is **logic-fixable** (a non-stalling cap), not just placement.
  Caveat: getbits (unthrottled) ran millions without the over-issue lock, so its placement kept natural
  in-flight < 9 — i.e. whether the *over-issue* lock (A) bites is still placement/timing-influenced; but the
  *throttle-hold* wedge (B) is the throttle, reproducibly.
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

## Recommended path (priority order) — UPDATED 2026-06-25 after cap=6 confirmed B = throttle-hold

The dominant blocker B is now known to be the throttle's **read-HOLD gapping the command stream** (the cap=4
build did this @~84 reads, and cap=6 reproduced it at the same point). So the fix is a **non-stalling in-flight
cap** — logic, sim-validatable. New priority:

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

## Sim oracle gaps to remember

`core/sim/memshim/ddr3_model.v` models failure A (`lock_threshold`) and lost reads (`+ddr_drop`), but does
**NOT** model failure B (the placement-marginal command-accept wedge — a physical, not logical, effect). So
the sim CANNOT validate a B fix; B must be judged on HW (counters + SignalTap). This is exactly why the
sim "proved" `READ_LIMIT=4` yet HW wedged.
