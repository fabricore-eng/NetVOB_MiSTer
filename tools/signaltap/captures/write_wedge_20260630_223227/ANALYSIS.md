# Write-wedge SignalTap capture — analysis + VERDICT (2026-07-01, de10)

Capture: `write_wedge_20260630_223227.csv` (8194 samples @clk_mem 108MHz, trig on `wedged`
high, post-position). Instrumented build = age-gate + st_waitreq obs reg (md5 47ecf5fb).
Decoded with 573 `read_stp_csv.py` (calibrated shift=+0, structural/validity=1.0, no drop).

## What happened (heisenbug PASSED — the build still wedges)
The trigger FIRED (IDLE→FILL→PRE→DONE). Onset at sample 7169:
`ram_write=1 state=1 wr_count=4(frozen) outstanding_reads=4 wr_stall_cnt 249→256→wedge`, with
**`st_waitreq=0` for ALL 8193 samples** (never captures the busy the FSM acts on).
UART corroboration: `M:D U:1 W:0004(frozen) P:001C RP:0000 PC:A21C`
= `{wedged=1, lock_cmd=WRITE, lock_outstanding=4, recovery_count=28}`.

## Mechanism THIS build manifested = READ-return-drop wedge (NOT the pure-write clear)
28 reads issued, **ZERO real read responses** (RP:0000, recovery synthesized 28), reads pegged at
outstanding=4 (=READ_LIMIT), then a write presented behind the un-drained reads → the f2sdram
refuses it (waitrequest high per the FSM) → `wr_stall_cnt` hits 256 → `wedged` latches. This is the
2026-06-25 "write blocked behind an outstanding read whose response was lost" mechanism — a DIFFERENT
manifestation than mister's 2026-07-01 pure-write wedge (P:0000). **Same disease, different boundary
signal → confirms the PLACEMENT LOTTERY nature: the marginal f2sdram-boundary path MOVES between builds.**

`st_waitreq` stuck-0 while the FSM's combinational `ddr3_waitrequest` reads busy = the boundary
capture is itself unreliable (a freshly-added boundary reg, placed with a hold-marginal path, reads
wrong) — direct on-silicon evidence that per-signal registers at this boundary work only by placement luck.

## ROOT CAUSE (STA + SDC, the important finding)
- The build has large reported HOLD violations (worst **−59.108 ns**) on the `sys_pll`/`pll_audio`/
  `h2f_user0_clk` domains. SignalTap is NOT the cause (0 sld/jtag paths in the STA).
- `mpeg2fpga_holdfix.sdc` (prior context): the `mem_shim → f2sdram_safe_terminator` command/addr hop
  is a TRUE **intra-clk_mem reg→reg HOLD path** with only **+0.64ns** margin — STA-passing but so tight
  that ANY placement perturbation tips it functional → WEDGE. Prior levers (set_min_delay, LCELL) all failed.
- **NEW LEAD (verified from the SDC):** `sys_top.sdc`'s `set_clock_groups -exclusive` glob is
  `*|pll|pll_inst|altera_pll_i|*[*].*|divclk`, but this core's PLL is `emu|sys_pll|altera_pll_i|...`.
  **The glob does NOT match `sys_pll`** → clk_mem (the f2sdram/mem_shim domain) is NOT decoupled from the
  async h2f/audio/hdmi/FPGA_CLK domains → the STA analyzes FALSE cross-domain paths → the −59ns hold
  "violations" → the fitter burns routing delay "fixing" them (Info 188005 ×2) → perturbs the +0.64ns
  bridge hop's placement → the wedge lottery. The −59ns MUST be cross-domain (an intra-clk_mem path is
  capped at the 9.26ns period), which pins it on the ungrouped sys_pll.

## VERDICT vs the original decision tree
The capture did NOT cleanly isolate command-out / waitrequest-in / bridge-side, because the
instrumented build re-rolled into a READ-return wedge, not the write wedge. But it revealed the deeper
truth: **the wedge is systemic f2sdram-boundary hold/placement marginality (a +0.64ns intra-clk_mem
knife-edge), and the placement is perturbed by the fitter chasing FALSE cross-domain hold violations
caused by a clock-groups glob that misses `sys_pll`.**

## RECOMMENDED NEXT STEP (cheapest-first, root-cause-oriented)
1. **Fix the SDC clock-groups coverage of `sys_pll`** (add `-group [get_clocks {*|sys_pll|altera_pll_i|*[*].*|divclk}]`
   to the exclusive set, or broaden the glob). No RTL. Rebuild → confirm the −59ns false violations
   vanish + 188005 drops → HW-test whether the wedge stops (placement no longer perturbed). SAFE: the
   f2sdram interface is single-domain (clk_mem, no CDC by design), so cutting sys_pll↔async is the
   intended-but-omitted decoupling, not masking a real path.
2. If the +0.64ns hop is still marginal after 1: pin/back-annotate a hold-clean placement of the bridge
   boundary (LogicLock is license-blocked; use incremental-compile / location back-annotation).
3. 2-deep skid buffer is now LOWER priority: it adds MORE logic at the exact knife-edge boundary and
   would re-roll the same hold lottery unless the clock-groups + placement are fixed first.

Artifacts: this CSV, quartus_stp log; scratchpad CAPTURE_INTERPRETATION.md + AGEGATE_BASELINE.md.
