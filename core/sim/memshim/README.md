# core/sim/memshim — decode-THROUGH-mem_shim co-sim (HW black-screen repro attempt)

This harness drives the **mpeg2fpga MPEG-2 decoder through the REAL Cyclone V
`mem_shim.sv`** (`core/MiSTer_MPEG2/rtl/mem_shim.sv`) instead of the bench
`mem_ctl.v`, against a **behavioral Avalon-MM / f2sdram DDR3 slave we wrote**
(`ddr3_model.v`) with realistic multi-cycle `waitrequest` and a decoupled,
N-cycle-later `readdatavalid`. The goal is the one stated in CLAUDE.md and in the
two prior static analyses: **reproduce or localize the hardware black-screen by
exercising the hazardous mem_shim paths the zero-latency bench `mem_ctl.v`
structurally cannot.**

This is a separate, single-writer directory; it does NOT touch `core/sim/run*`,
the shared `core/sim/Makefile`, or any other agent's dir. It reuses the *proven*
`core/sim` decode pipeline (pristine `mpeg2fpga` RTL + the bench SOFT FIFOs — the
exact path that already decodes greyramp to a PNG) and the shared helper scripts
(`../prep_stream.sh`, `../framestore_extract.py`).

## TL;DR finding

**Rung reached: `frame-png`. decode_through_memshim = `correct-frame`.**

The decoder decodes greyramp to a **correct frame THROUGH the real `mem_shim.sv`**,
and the decoded output is **byte-identical** whether the DDR3 model is
zero-latency (bench-like) or realistic (8-cycle read latency, `waitrequest` high
7-of-8 cycles, ±7-cycle random jitter). **No stall. No FSM hang. No
tag/response-FIFO desync. No `$stop`. No out-of-bound address. No window
violation.** See "What this does and does NOT prove" — this is a real negative
result that **narrows** the HW-bug search space; it does not by itself explain
the reported HW black-screen.

| Rung | Reached | Evidence |
|------|---------|----------|
| analysis-only | yes | this README + the two prior analyses it builds on |
| harness-built | yes | `tb_memshim.v` + `ddr3_model.v` + `Makefile` + `run_memshim.sh` |
| compiles | yes | `make build` → `obj_dir/Vtb_memshim` (lint exit 0) |
| runs | yes | `make run` → `run/framestore_*.ppm` + `run/tv_out_*.ppm` |
| frame-png | yes | `artifacts/memshim_*_frame0_Y.png` (correct greyramp THROUGH mem_shim) |
| reproduced-black | **NO** | the black-screen did **not** reproduce in this co-sim (see below) |

## Topology (what is actually wired)

```
stream.dat ($readmemh)  ── stream_data/stream_valid ──►  mpeg2video (pristine mpeg2fpga)
                                                            │  (clk=27 MHz, dot_clk=27 MHz)
                                       framestore.v contains the dual-clock CDC FIFOs:
                                         mem_request_fifo  (wr@clk 27 → rd@mem_clk 108)
                                         mem_tag_fifo      (parallel tag stream)
                                         mem_response_fifo (wr@mem_clk 108 → rd@clk 27)
                                                            │  mem_clk = 108 MHz
                       mem_req_rd_* / mem_res_wr_*  ◄──────►  mem_shim.sv  (REAL, unmodified)
                                                            │  Avalon-MM
                       ddr3_addr/read/write/waitrequest/readdatavalid ◄──► ddr3_model.v
```

- **Clocking matches the real design (`emu.sv`), NOT the bench.** `clk=27 MHz`,
  `mem_clk=108 MHz`, `dot_clk=27 MHz` (`emu.sv:338-340`). The bench used
  75/125/27. Using 27/108 reproduces the true CDC ratio the framestore FIFOs see.
- **The CDC FIFOs live inside `mpeg2video.framestore`** (the bench soft `fifo_dc`,
  genuinely dual-clock: `generic_fifo_dc.v`, gray-code pointer CDC, standard-mode
  1-cycle read latency, `valid = rd_en & ~empty` registered on `rd_clk`). So the
  decoder↔shim contract here is byte-for-byte the request-FIFO read port +
  response-FIFO write port that the prior analyses identified. The **only** changed
  variable vs the known-good `core/sim` run is the memory controller behind those
  FIFOs (`mem_ctl.v` → `mem_shim.sv` + `ddr3_model.v`), so any stall would be
  unambiguously attributable to the shim/DDR3 timing.
- **`mem_shim.sv` is unmodified** and driven straight from its committed path —
  no patch, gitlink stays pinned. (`core/patches/hw/` holds an unrelated emu
  patch; this harness needed no submodule edit.)

## The DDR3 model (`ddr3_model.v`) — the piece that did not exist in-tree

A behavioral MiSTer "DDRAM" / Intel HPS-to-FPGA SDRAM-bridge slave:

- `ddr3_waitrequest` (= `DDRAM_BUSY`): when HIGH the slave is **not** accepting a
  command this cycle; a command is accepted on the first cycle where
  `(ddr3_read|ddr3_write) && !ddr3_waitrequest`. Configurable: busy for `(P-1)` of
  every `P` cycles.
- **Read = split transaction**: an accepted read schedules `ddr3_readdatavalid` +
  `ddr3_readdata` **N cycles later** (configurable base latency + optional random
  jitter), decoupled from command acceptance. **Writes produce no readdatavalid**
  (matches the tag-FIFO contract: only reads yield responses).
- Backing store indexed by the **decoder word address** (`ddr3_addr[21:0]`), so the
  framestore-dump task reads decoded frames straight out of DDR3 — proving
  decode-through-mem_shim by inspecting what actually landed in "DDR3".
- **Instrumentation**: `rd_issued`/`rd_responded`/`wr_issued` counters, per-event
  `+ddr_trace` log, **out-of-bound address flag** (word addr > `END_OF_MEM`), and
  **window-bits check** (`ddr3_addr[28:22]` must be `7'b0011000`).

Knobs (Verilator `+plusargs`): `+ddr_rd_latency=N`, `+ddr_wait_period=P`,
`+ddr_rd_jitter=J`, `+ddr_zero_latency` (bench-emulation control), `+ddr_trace`.

The testbench adds a **progress watchdog**: if neither `macroblock_address` nor a
DDR3 response advances for `+wd_cycles` mem_clk ticks, it dumps a full
localization report (mem_shim FSM `debug_state`, skid `saved_cmd`, `rd/wr/rsp`
counts, `ddr3_read/write/waitrequest/readdatavalid`, decoder `busy/error/mb_addr`)
and `$finish`. The decoder's own tag-sync checker
(`framestore_response.v:248-253`, `$stop` on response/tag desync) is also live.

## How to run

```sh
cd core/sim/memshim
make build                       # → obj_dir/Vtb_memshim  (lint is implied)
make stream.dat                  # reuses ../stream.dat (greyramp) if present
# control (bench-like; should match the known-good core/sim decode):
./run_memshim.sh run_zero 2 300 -- +ddr_zero_latency
# realistic latency only:
./run_memshim.sh run_lat8  3 360 -- +ddr_rd_latency=8 +ddr_wait_period=0
# realistic latency + waitrequest:
./run_memshim.sh run_wait  3 360 -- +ddr_rd_latency=8 +ddr_wait_period=4
# harsh stress (long latency + heavy waitrequest + jitter):
./run_memshim.sh run_harsh 3 420 -- +ddr_rd_latency=20 +ddr_wait_period=8 +ddr_rd_jitter=7
# trace the bus (small clip, 1 frame):
./run_memshim.sh run_trace 1 90 -- +ddr_rd_latency=8 +ddr_wait_period=4 +ddr_trace
# render a decoded Y plane to PNG:
python3 ../framestore_extract.py run_lat8/framestore_0001.ppm run_lat8/yp --prefix lat8
```

`run_memshim.sh RUNDIR MIN_FRAMES MAX_WAIT_SEC -- <plusargs...>` launches the sim
detached and stops it once `MIN_FRAMES` `framestore_*.ppm` exist OR a STALL/END
report appears (macOS has no `timeout`). **Note:** the *last* framestore file is
mid-write when the sim is killed; use `MIN_FRAMES=N+1` to guarantee
`framestore_<N-1>.ppm` is closed (`# not truncated` tail). The PPMs are ~54 MB.

## The experiment and the result

Four DDR3-timing regimes, all decoding the shipped 720x576 PAL-interlaced
greyramp ES through the real mem_shim:

| Run | `rd_latency` | `wait_period` | `jitter` | Frames | `framestore_0001.ppm` |
|-----|----:|----:|----:|:------:|---|
| `run_zero`  (control) | 0  | 0 (never) | 0 | 2 | MD5 `88bf3514…` |
| `run_lat8b`           | 8  | 0 | 0 | 3 | MD5 `88bf3514…` **(identical)** |
| `run_waitb`           | 8  | 4 (busy 3/4) | 0 | 3 | MD5 `88bf3514…` **(identical)** |
| `run_harsh`           | 20 | 8 (busy 7/8) | ±7 | 3 | valid greyramp (snapshot offset) |

- The first complete decoded frame (`framestore_0001.ppm`) is **byte-for-byte
  identical** across zero-latency, lat8, and wait4. The decoded FRAME_0 Y plane
  hashes identically (`md5 = 41b4b17d…`) for control vs lat8.
- `run_harsh` produces the same correct greyramp (FRAME_0 Y mean 124.4, a clean
  black→white gradient — see `artifacts/memshim_harsh_frame0_Y.png`); its
  `framestore_0001` hash differs only because the slower run captured the frame at
  a different frame-buffer-rotation snapshot, not because pixels differ.
- The `+ddr_trace` run confirms: **0 out-of-bound, 0 window violations**; every
  address lands in window 3 (`0x06xxxxxx`, e.g. `0x061c0000`, `0x061efffe`);
  `rd_issued == rd_responded` throughout (no dropped/extra/reordered responses);
  the decoder's startup VBUF clear writes `0x1efff4..0x1efffe` — i.e. it stops
  **one word short of ADDR_ERR** (`0x1effff`) and **never accesses ADDR_ERR** in
  this stream.

**Verdict: decode-through-mem_shim = `correct-frame`. The HW black-screen did NOT
reproduce in this co-sim, across timing regimes far harsher than a real
f2sdram bridge.**

## Why it works in sim — and exactly what is therefore ruled IN / OUT

`mem_shim.sv` is **strictly single-outstanding**: on a read/write in `S_IDLE` it
sets `state<=1` (`S_WAIT`) and issues no further command until `!ddr3_waitrequest`
returns it to `S_IDLE` (`mem_shim.sv:116-202`, `ddr3_burstcnt=1`). With **one**
DDR3 transaction in flight and a slave that returns responses **in order, exactly
once per accepted read**, the response stream **cannot** reorder, drop, or
duplicate — so the `mem_response_fifo`/`mem_tag_fifo` count+order invariant
(`framestore_response.v:248-253`) is preserved structurally. The realistic
latency/waitrequest only *slows* the pipeline (the framestore FIFOs absorb it via
backpressure: `mem_req_rd_en = !mem_res_wr_almost_full`); it does not corrupt it.

This is a partial **refutation** of the two prior analyses' lead hypotheses *for a
well-behaved bridge*:

- **Tag/response desync (their MECHANISM 1 / Mismatch #1):** does **not** fire
  given single-outstanding + in-order + exactly-once responses. It can only fire
  if the real bridge **reorders, drops, or duplicates** `readdatavalid`, or if a
  late `readdatavalid` survives across a watchdog reset — none of which a correct
  Avalon slave does, and none of which this model injects.
- **The synthetic-ADDR_ERR intra-cycle collision (their MECHANISM 1, lines
  109 vs 139-140/175-176):** **not exercised** by greyramp — the decoder never
  reads `ADDR_ERR` in this stream. It remains a real, un-cleared hazard for streams
  whose motion vectors/macroblock addresses *do* generate `ADDR_ERR` reads
  (overflow sentinel); this harness simply never reached it. (Note the genuine
  RTL smell: `mem_res_wr_en` is assigned both by `mem_res_wr_en <=
  ddr3_readdatavalid` at `:109` and by the synthetic `mem_res_wr_en <= 1'b1` at
  `:140/:176` in the same always block — if a real `readdatavalid` lands the same
  cycle as an ADDR_ERR read is processed, the synthetic write wins and one real
  response is dropped. Worth fixing defensively even though it's unreached here.)
- **Address/window/TrustZone (their Mismatch #4):** **confirmed correct** in sim —
  `{7'b0011000, addr}` keeps every access in window 3 within the 15.5 MB bound; 0
  OOB. (Sim cannot confirm the *absolute* CMA base `0x30000000` or word-vs-byte
  granularity at the real bridge — that needs HW or the bridge spec.)

## What this does and does NOT prove (be honest)

**Proves (in sim):**
- The harness and the proven decode path are correct: zero-latency mem_shim
  reproduces the known-good greyramp frame.
- `mem_shim.sv`'s FSM + skid buffer + address packing are functionally correct
  against a **conformant, single-outstanding, in-order, exactly-once** Avalon-MM
  slave with realistic latency and heavy waitrequest. It does not stall or corrupt.

**Does NOT prove / out of reach here:**
- It does **not** reproduce the reported HW black-screen, so it does **not**
  identify its root cause. The remaining suspects are exactly the things a
  *behavioral, conformant* DDR3 model cannot represent:
  1. **Real f2sdram response behavior** — if the MiSTer DDRAM bridge can reorder,
     drop, coalesce, or emit `readdatavalid` not-exactly-once-per-read (or has a
     longer pipeline than mem_shim's single-outstanding FSM assumes), the tag
     desync DOES fire. Needs the bridge spec or an on-HW capture of
     `rd_count`/`rsp_count` over UART (`mem_shim.sv:219-236`).
  2. **The ADDR_ERR collision path**, on a stream that actually reads ADDR_ERR.
  3. **Watchdog-reset / late-response** races (`framestore_response.v:116-122`
     flush vs in-flight `readdatavalid`) — not modeled; needs a reset-during-traffic
     scenario.
  4. **The fork's mpeg2video + Xilinx-FIFO build** — this harness deliberately uses
     the pristine decoder + bench SOFT FIFOs (the only Verilator-clean path). The
     fork's `framestore.v` hard-wires `FIFO_XILINX(1)`; a desync rooted in the
     Xilinx FIFO's exact `prog_full`/`valid` timing would not appear here.
  5. **Absolute address base / clock-domain PLL phase / DDR3 init** — pure HW.
- The "prior-working-config produced video" claim and "current shim Not yet
  compiled" (`BUG_FIX_LOG.md:320`) remain **unverifiable** without LAN/Quartz/board.

## Fix hypothesis (ranked, from this sim + the analyses)

1. **The shim is correct for a conformant bridge; the HW bug is most likely a
   bridge-behavior assumption, not a shim FSM bug.** Highest-value next step is an
   **on-HW capture of `debug_rd_count` vs `debug_rsp_count`** (UART) under a real
   decode: if `rsp_count` diverges from `rd_count`, the bridge is not
   exactly-once/in-order and mem_shim's single-outstanding assumption is violated —
   then add a **response-vs-request balance assertion + a bounded reorder buffer**
   in the shim. To reproduce *in sim*, extend `ddr3_model.v` with an
   out-of-order / drop / duplicate-readdatavalid mode and re-run; the decoder's
   own `framestore_response.v:248` `$stop` will catch it immediately.
2. **Defensively fix the ADDR_ERR / readdatavalid same-cycle collision**
   (`mem_shim.sv:109` vs `:140/:176`): gate the synthetic response so it cannot
   stomp a real `ddr3_readdatavalid` in the same cycle (e.g. hold it one cycle, or
   `mem_res_wr_en <= ddr3_readdatavalid | synthetic_pulse` with the data muxed and
   `synthetic_pulse` suppressed when `ddr3_readdatavalid`). Then add an ADDR_ERR-
   generating test stream (large motion vectors) to this harness to exercise it.
3. **Add a reset-during-traffic scenario** (assert `rst_n` mid-decode while reads
   are outstanding) to test the flush/late-response race that `mem_shim` cannot
   cancel.

## Files

- `tb_memshim.v` — top: pristine `mpeg2video` + real `mem_shim` + `ddr3_model`,
  real-design clocks, stream feed, tv_out + framestore PPM dumps (reusing the
  bench dump logic but reading `ddr3.mem[]`), progress watchdog + stall report.
- `ddr3_model.v` — the behavioral Avalon-MM/f2sdram slave (knobs + instrumentation).
- `Makefile` — ladder targets; same RTL list as `../Makefile` minus `mem_ctl.v`,
  plus `mem_shim.sv` (`.sv`→SystemVerilog) and the two harness files.
- `run_memshim.sh` — detached run + frame/stall-gated stop + plusarg passthrough.
- `artifacts/` — durable evidence (tracked): correct-frame PNGs for the
  zero-latency / lat8 / harsh regimes + a trace excerpt.
- `run*/` — scratch (gitignored): PPMs, PNGs, `run.log`.
