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
| B | **Command-accept (waitrequest) wedge** | Option-A build wedged @85 reads on a WRITE, BUSY stuck, 1 in-flight (PC:A081) | placement-marginal (physical) |
| C | **Lost-read desync** | reads occasionally dropped (VN−VL≈1; recovery fired) → positional misalign | bridge drops ~0.1% of responses |

- **A** needs an in-flight cap **below ~9**. The old `READ_LIMIT=4` was tuned to a *sim-assumed* lock@6;
  HW shows ~9, so 4 over-throttles (clamps the pipeline lower than necessary).
- **B** is the dominant killer and is **placement-dependent**: getbits's placement had a good command-accept
  path (decoded partial); the Option-A build's placement wedged early; the unthrottled build's was decent
  (ran ~6000). It is a *physical* fragility of the fabric↔HPS handshake — **no logic change un-sticks a
  physically wedged bridge.**
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

## Recommended path (priority order)

1. **Localize failure B with SignalTap** (the project's observe-first rule; handoff §8, `fabricore:signaltap`
   skill, de10 JTAG bench). Capture `DDRAM_*` at the wedge to answer: does the bridge stop *accepting*
   (waitrequest stuck — physical command-accept fragility) and/or genuinely *drop* a read response
   (DOUT_READY never pulses for an accepted read)? This decides whether the fix is placement-robustness vs a
   bridge-config/protocol change — and avoids more blind 35-min build gambles.
2. **Cheap, data-driven build while/if observing isn't ready:** `READ_LIMIT=6` (below the HW lock@9, above
   getbits's natural working set; sim-validated decode byte-identical, in-flight peaks at 6, no lock at
   threshold=9). Removes failure A as a variable. Will NOT fix B (placement) — so treat its result as a
   B-probe (does this placement wedge on command-accept?).
3. **Placement robustness for B** — once SignalTap says it's command-accept: options are (a) back-annotate a
   *good-placement* build's location assignments to freeze it (needs a build that decodes first; getbits's
   CDB is gone), (b) a correctly-registered waitrequest handshake that doesn't double-accept (the naive
   version double-issues — needs the d_accepted-style alignment the command-register patch used, which
   failed, so approach with care), or (c) escalate the f2sdram bridge config (clock/CSR/burst — see the
   RocketBoards "write-ok-read-stuck" thread: the controller buffers reads and latency spikes after refresh).
4. **Correct lost-read recovery (failure C)** — implement the reorder-buffer re-issue above and DELETE the
   zero-fill, *after* a good placement decodes (this is what turns getbits's "partial then desync" into a
   full clean decode). Sim-validate against `+ddr_drop` in `core/sim/memshim` (decode byte-identical, no
   zero-fill, in-flight bounded).

## Sim oracle gaps to remember

`core/sim/memshim/ddr3_model.v` models failure A (`lock_threshold`) and lost reads (`+ddr_drop`), but does
**NOT** model failure B (the placement-marginal command-accept wedge — a physical, not logical, effect). So
the sim CANNOT validate a B fix; B must be judged on HW (counters + SignalTap). This is exactly why the
sim "proved" `READ_LIMIT=4` yet HW wedged.
