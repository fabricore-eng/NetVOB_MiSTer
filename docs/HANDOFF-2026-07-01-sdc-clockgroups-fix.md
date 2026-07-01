# HANDOFF — 2026-07-01 — SDC clock-groups fix BUILT + timing-clean; HW decode test is the next step

**Read this first, then [`docs/progress.md`](progress.md) (bottom ~6 entries) and the recalled
session memories. This SUPERSEDES `HANDOFF-2026-07-01-signaltap-writewedge.md` (now stale).**

---

## TL;DR — one line
The write-wedge was SignalTap-captured on de10 and root-caused to a **stock SDC clock-groups glob
that never matched this core's `sys_pll`** → `clk_mem` (the f2sdram/mem_shim domain) was never
decoupled from the async HPS/audio/HDMI domains → false −59ns cross-domain hold "violations" → the
fitter burned routing delay "fixing" them → that perturbation tipped the genuinely knife-edge
(+0.64ns) intra-`clk_mem` `mem_shim → f2sdram_safe_terminator` command hop → the placement-lottery
WEDGE. **The one-line SDC fix (group `sys_pll`) is BUILT and TIMING-CLEAN. The immediate next step is
the on-hardware decode test on the SuperStation — it needs the human's explicit in-session "go".**

## THE IMMEDIATE NEXT STEP (needs the human's explicit "go")
Run the objective decode gate for the SDC-fix build on the SuperStation:
```
~/Dev/fabricore/NetVOB_MiSTer/tools/build/hw_flash_and_gate.sh sdcfix
```
It: converts the fresh `dell:output_files/mpeg2fpga.sof` → tagged `.rbf`, acquires the **mister**
devlock (GATE), **warm-reboots** (now with the FIXED reboot-race — confirms DOWN before polling UP),
flashes, writes an abspath `.mgl` (delay 2), `load_core`, reads UART, dumps the `/dev/mem`
`0x30000000` framestore, and runs the SSIM decode gate. mister is shared with 573 → gate on a
SUCCESSFUL devlock, coordinate via `dell_coord.sh chat`.

### What to expect / the decision tree
- **DECODES a frame** (W climbs past the framestore clear, `U:0`, `RP` tracks `P`, framestore SSIM
  gate PASS): the root cause is confirmed end-to-end → **milestone**. This is the weeks-long blocker
  falling. Then move down the ladder: desync/correctness → decode-correctness SSIM≥0.95 → scanout
  ([[scanout-blind-spot-ddr-vs-crt]]).
- **STILL WEDGES** (`U:1`, W frozen, `PC>=0x8000`): the +0.64ns hop is marginal even with clean
  constraints → the SDC fix removed the *perturbation* but not the *knife-edge* itself → next lever =
  **pin/back-annotate a hold-clean placement** of the bridge boundary (LogicLock is license-blocked on
  free 17.0 Lite per [[placement-fix-no-logiclock]]; use incremental-compile / location back-annotation).
  The 2-deep skid is DEMOTED (adds logic at the knife-edge — re-rolls the lottery).
- Warm-reboot before ANY verdict (non-negotiable); the decode verdict = DDR framestore dump vs
  `core/sim/artifacts/decode_ref_set/`, NOT the CRT/HDMI (beware the flat-field SSIM≈0.70 false-positive).

## What this session did (arc)
1. Built + captured the instrumented write-wedge SignalTap probe on de10 (heisenbug PASSED — trigger
   fired). CSV: `tools/signaltap/captures/write_wedge_20260630_223227/` (+ `ANALYSIS.md`).
2. The capture manifested a **read-return-drop** wedge (28 reads / 0 real responses, UART `RP:0000`,
   recovery=28, reads pegged at outstanding=READ_LIMIT, a write then wedged behind them), NOT the
   pure-write clear mister showed → the marginal boundary path MOVES between builds = **placement
   lottery confirmed**. `st_waitreq` read stuck-0 all 8193 samples → the boundary capture itself is
   hold-unreliable.
3. STA: worst HOLD slack **−59.108 ns** on sys_pll/pll_audio/h2f (NOT SignalTap — 0 sld paths). Found
   the `set_clock_groups` glob (`sys_top.sdc:14` = `*|pll|pll_inst|...`) does not match `emu|sys_pll|...`.
4. FIX: added `-group [get_clocks {*|sys_pll|altera_pll_i|*[*].*|divclk}]` to `mpeg2fpga_holdfix.sdc`.
   Built clean (0 err). **Timing verification (decisive):** stock glob CONFIRMED broken (`Warning 332174:
   ... sys_top.sdc(14) ... could not be matched with a clock`); my sys_pll glob MATCHED; worst hold
   **−59.108 → +0.132ns** (all corners positive); `188005` warnings **2 → 0**; hold-fix routing delay
   6.5% → 1.9%. **CAVEAT:** worst SETUP is now tight **+0.040ns** (positive/signed-off — watch it).
5. Tooling: fixed `hw_flash_and_gate.sh` reboot-race (confirm DOWN before UP); added
   `tools/signaltap/capture_dvd.sh` (push-button de10 capture).

## Artifacts & state (branch `feat-decoder-bringup`, all pushed)
| Commit | What |
|---|---|
| `0264eef` | hw_flash_and_gate.sh reboot-race fix |
| `8c1b271` | capture_dvd.sh (de10 SignalTap capture) |
| `a436ca0` | write-wedge captured + root-caused (progress + ANALYSIS.md + CSV) |
| `227a471` | **the SDC clock-groups fix + timing verification** |

- **dell is in a DEPLOYABLE state** for the HW test: `mem_shim.sv`=`4a8ca207` (age-gate), `mpeg2fpga.qsf`
  = clean (0 signaltap; instrumented backup at `mpeg2fpga.qsf.signaltap_bak`), `mpeg2fpga_holdfix.sdc`
  has the sys_pll fix, fresh `output_files/mpeg2fpga.sof` (Jul 1 06:28 = the SDC-fix build).
- Submodule (`core/MiSTer_MPEG2`) is non-pushable → the SDC fix lives as a re-appliable patch:
  `core/patches/hw/mpeg2fpga-sdc-clockgroups-syspll.patch`. mem_shim variants + baseline are saved in
  the (ephemeral) session scratchpad; canonical age-gate is reconstructable via
  `core/patches/hw/mpeg2fpga-memshim-write-gate.patch` (baseline 4de9dcd1 + patch = 4a8ca207).

## Sim fix-validation baseline (for ANY future mem_shim change)
Age-gate (4a8ca207) sim baseline @latency30, dense clip (`core/sim/memshim/stream.dat`): decodes 3
frames at BOTH `+ddr_wait_period=0` AND `=2`, identical trajectory. **Byte-identical settled-frame
fingerprints (the gate): framestore_0000.ppm = `20d53898`, framestore_0001.ppm = `01d88a70`** (both
54.19MB, identical across wp). Run to MIN_FRAMES≥4 and compare settled frames (the last-before-kill
frame is a truncated partial — ignore it). Validate any mem_shim fix byte-identical vs this at BOTH
wp=0 AND wp=2 before spending a build (a 1-deep slice passed wp=0 but DROPPED writes at wp=2).

## Non-negotiables / gotchas (durable)
- HW gate needs the human's **explicit in-session "go"** each time; gate every disruptive board action
  on a SUCCESSFUL devlock (mister/de10 shared with 573). Warm-reboot before ANY verdict.
- Build ONLY via the hub launcher (`DELL_PROJECT=dvd DELL_TARGET=mpeg2fpga
  DELL_REPO=NetVOB_MiSTer/core/MiSTer_MPEG2 ~/Dev/fabricore/tools/tools/dell_build.sh` — NO ref =
  builds dell's working tree with scp'd edits). A PreToolUse hook blocks off-protocol builds AND
  false-positives on commit messages containing `ssh <host> reboot`-like text → commit via a message file.
- UART legend (115200 8N1, `/dev/ttyS1`): `W`=writes `P`=reads `RP`=responses `U`=waitrequest
  `M`=shim_state `PC`={wedged,lock_cmd,lock_outstanding,recovery_count}. Wedge = `U:1` + W/P frozen +
  `PC>=0x8000`.
- Chat watcher is event-driven; RE-ARM after every turn; do NOT create a periodic re-arm cron (the
  updated protocol forbids it — it floods transcripts).
