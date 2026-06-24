# HANDOFF — next step: fix the placement-marginal f2sdram bridge → first FULL HW decode

**Date:** 2026-06-24 · **Branch:** `feat-decoder-bringup` · **Session key:** `dvd`
**Read first:** this doc is self-contained. Background only if needed: [`PLAN.md`](../PLAN.md),
[`docs/progress.md`](progress.md) (newest entries), [`docs/hw-bridge-wedge-fix-plan.md`](hw-bridge-wedge-fix-plan.md).
Memories: `bridge-placement-marginal-root-cause`, `placement-fix-no-logiclock`,
`dell-build-mechanics-no-push`, `never-mask-faults-with-fake-data`.

---

## 1. Where we are (one paragraph)

The MPEG-2 decode pipeline is logically correct (proven in sim and partially on HW). The **only**
thing blocking a full decoded frame on hardware is that the **HPS f2sdram memory-bridge interface is
placement-marginal**: each compile places the fabric↔HPS bridge logic differently, and most
placements break it. The goal of this next step is a **deterministic good placement** (or a
placement-independent hold-margin fix) so a build of the recovery netlist decodes reliably.

## 2. The blocker, root-caused on silicon (don't re-derive)

Via an `emu.sv` counter-probe (raw `DDRAM_*` counters on uart `VL/VN/BL/BN`), in a **bad placement**:
- **Writes work** (`BN` ~2M — the framestore clear completes).
- **Reads get accepted** (`VN`, `P`) but are **NEVER answered**: `VL=0` (zero raw `DDRAM_DOUT_READY`
  pulses), `RP=0`. Then the bus jams busy-forever (`BL` climbs, `DDRAM_BUSY` stuck, `PC` wedged).
- `VL=0` **at the raw pin** proves the bridge is SILENT on reads — NOT that mem_shim miscounts. So it's
  a **physical fabric↔HPS interface fragility (most likely hold margin on the read-return signals)**,
  not a logic bug.
- 3 placements tried this session = 3 distinct failures (rebaseline=blank, counter-probe=dead
  read-return, SEED-5=write-side wedge with zero reads). **Seed-roulette is unreliable — do not just
  re-roll seeds.**
- **`getbits.rbf` (md5 `ea955179`, on dell) is the ONLY known-good placement** — it decodes (partial,
  top MB-rows pixel-correct vs golden) but LACKS the mem_shim recovery, so it desyncs/stops partway.

## 3. The fix plan (CORRECTED — read the don'ts in §6)

### Option A (RECOMMENDED, untried): register the mem_shim↔bridge handoff for HOLD MARGIN — *placement-independent*
The bridge is Altera hard IP (no regs inside), but `mem_shim` is ours. mem_shim already **registers its
outputs** (`ram_read/ram_write/ram_address` are regs) but uses the bridge **inputs combinationally**:
`ddr3_readdatavalid` (=`DDRAM_DOUT_READY`), `ddr3_readdata` (=`DDRAM_DOUT`), `ddr3_waitrequest`
(=`DDRAM_BUSY`). The dead path is exactly the **read-return** (`DOUT_READY`/`DOUT`). Add an input
pipeline register on those so the HPS-pin→logic path is registered (hold margin) and stops depending
on placement.

Concretely (in `core/MiSTer_MPEG2/rtl/mem_shim.sv`, clk = `clk_mem`):
1. Register `ddr3_readdatavalid` + `ddr3_readdata` once at the input boundary
   (`reg rdv_q; reg [63:0] rdd_q; always @(posedge clk) begin rdv_q<=ddr3_readdatavalid; rdd_q<=ddr3_readdata; end`)
   and drive the response path + `rsp_count`/`outstanding_reads` from `rdv_q`/`rdd_q` instead of the
   raw inputs. This delays each response by 1 cycle — adjust the response/recovery/count logic for the
   +1 (it's a uniform shift; the tag/response FIFO ordering is unchanged).
2. **Leave `ddr3_waitrequest` handling for a second iteration** — registering it changes command
   acceptance timing (the FSM samples it to accept), which is riskier; the read-return path is the
   proven-dead one, so fix that first and re-test.
3. **Validate in the memshim sim oracle first** (`core/sim/memshim/`, `make build` + `./run_memshim.sh`)
   — confirm the registered version still decodes cleanly (it should; the change is a uniform pipeline
   delay). Then build + flash + gate on HW.

This is the `docs/hw-bridge-wedge-fix-plan.md` "Step 2-alt" idea, and it directly targets the
read-return-dead finding. **If the fragility is hold/routing (likely), this is the real fix** and makes
placement irrelevant. (Note: the 2026-06-05 source-register attempt failed, but that was a *probe tap*
whose reg got placed bridge-adjacent by its input; registering mem_shim's OWN boundary is different —
the regs *are* mem_shim.)

### Option B (fallback): freeze a known-good placement via location-assignments (license-free, NOT LogicLock)
`quartus_cdb mpeg2fpga --back_annotate=lab` (in the docker) writes `set_location_assignment` lines into
`mpeg2fpga.qsf` that freeze a placement. **Recommended order:** build the recovery netlist once → decode-
gate it → **if it lands a good placement, back-annotate THAT build** (instance names match perfectly).
Do NOT chase getbits's placement: **getbits's compiler DB is GONE** (dell's `db/` is now the SEED-5
build); only its `.rbf` survives and you cannot back-annotate from a bitstream. Caveat: routing is NOT
frozen on the free edition (`.rcf` is LogicLock-gated) — if the fragility is routing/hold, Option A is
the real fix.

## 4. Exact commands (all verified this session)

**Stage a local RTL edit onto dell** (submodule origin is upstream `mrchrisster/MiSTer_MPEG2` → CANNOT
push; build is from dell's *working tree*):
```sh
scp core/MiSTer_MPEG2/rtl/mem_shim.sv dell:/tmp/mem_shim.sv.new
ssh dell "diff -u ~/NetVOB_MiSTer/core/MiSTer_MPEG2/rtl/mem_shim.sv /tmp/mem_shim.sv.new"   # confirm ONLY your change
ssh dell "cp /tmp/mem_shim.sv.new ~/NetVOB_MiSTer/core/MiSTer_MPEG2/rtl/mem_shim.sv"
# (current local==dell mem_shim.sv md5 = 217b0b6b...; READ_LIMIT=4 recovery already staged on dell)
```

**Build** (NO ref ⇒ compiles dell's working tree; passing a ref WIPES the uncommitted edits):
```sh
DELL_PROJECT=dvd DELL_TARGET=mpeg2fpga DELL_REPO=NetVOB_MiSTer/core/MiSTer_MPEG2 \
  ~/Dev/fabricore/tools/tools/dell_build.sh                 # --who / --status to check slots (cap 2)
# detached ~30min, log dell:/tmp/dellbuild-dvd.log, container quartus-dvd; produces output_files/mpeg2fpga.sof
```

**Deploy + objective decode gate** (BUILD first — the gate converts the *current* sof):
```sh
./tools/build/hw_flash_and_gate.sh <TAG>
# converts sof->tagged rbf, acquires mister devlock [GATE], WARM-REBOOTS (clears the stale HPS wedge —
# a load_core does NOT), load_core, reads uart, /dev/mem framestore dump, runs hw_decode_verify.sh.
# PASS iff full-frame SSIM>=0.95 AND %diff<=2.0 vs core/sim/artifacts/decode_ref_set/.
```

**Read the bridge counters from the uart line** (the diagnosis): `VL`=raw DOUT_READY pulses (responses),
`VN`=reads accepted, `BL`=DDRAM_BUSY cycles, `BN`=writes accepted; `P`/`RP`=mem_shim rd/rsp; `PC`=wedge
snapshot. **Good placement ⇒ VL>0 with reads answered; dead-bridge signature ⇒ VL=0 / BL climbing.**

**Cockpit (keep current):**
```sh
~/Dev/fabricore/tools/tools/dell_coord.sh status set dvd --stage build|verify|observe "<one-liner>"
~/Dev/fabricore/tools/tools/dell_coord.sh queue set dvd "label|gate|~est" ...   # rewrite as work advances (collapses >2h stale)
```

## 5. Verify discipline (DO NOT skip — I got burned)

- **Trust the numeric VERDICT, not the auto-pick.** `hw_decode_verify.sh` Step-2 auto-pick can latch a
  flat field and report a misleading flat-field SSIM ~0.70 — I wrongly called SEED-5 a "decode" off
  that; the objective `VERDICT=FAIL` (SSIM≥0.95 AND %diff≤2) was right. Also LOOK at the rendered frame
  (`/tmp/hw_gate_<TAG>/render/rendered/frame*_Y.png`) and check the bridge counters.
- **Warm-reboot before every verdict** (the flash script does it). **Reproduce before claiming** — never
  a milestone from one run.

## 6. Don'ts / gotchas (each cost time this project)

- **Do NOT use LogicLock** — it's license-BLOCKED on our free Quartus 17.0 Lite: emits Warning 292013 +
  Critical Warning 140003 and **silently removes all regions** (the build looks fine but nothing is
  pinned). Verified in `docs/hw-bridge-wedge-fix-plan.md`. Use Option B's location-assignments instead.
- **Do NOT push the submodule** — origin is upstream `mrchrisster/MiSTer_MPEG2`. Stage to dell via
  scp+diff+cp (§4). Snapshot any edited `mpeg2fpga.qsf` off-box (a build regenerates `db/`).
- **Do NOT re-roll seeds hoping for luck** — 3 placements gave 3 different failures; it's unreliable.
- **Do NOT mask a fault with fake data** (PROTOCOL rule 9 / `never-mask-faults-with-fake-data`). The
  mem_shim `resp_timeout` zero-fill is a band-aid; the real fix is correct pacing + a sound bridge
  interface. Consider removing/demoting the zero-fill once the bridge is fixed.
- **Probe placement re-rolls the bug.** Adding logic near the bridge re-rolls the marginal placement.
  Keep the counter-probe (it's small, in `emu.sv`, already there) but don't add bridge-adjacent taps.

## 7. What's already PROVEN / banked (don't redo)

- **Pacing is the real fix for the over-issue lock:** mem_shim `READ_LIMIT=4` caps in-flight reads at 4
  (sim oracle, realistic latency) below the silicon-observed 6-in-flight lock. The `resp_timeout`
  zero-fill is unnecessary.
- **Tooling:** the `hw_*.sh` scripts are repointed to the relocated hub (`~/Dev/fabricore/tools`).
- **The no-mask rule** is PROTOCOL rule 9 across all agents.
- **getbits** = the only known-good placement (decodes partial-correct; lacks recovery).

## 8. Deeper observation if you get stuck

If the gate alone can't localize a new failure, use the **`fabricore:signaltap` skill** (DE10 + JTAG →
dell, no native Quartus) to capture the live `DDRAM_*` / mem_shim signals at the wedge — the
observe-first alternative to fix-swing builds. The 573 session's runbook
(`~/Dev/fabricore/System573_MiSTer/tools/signaltap_573/RUNBOOK.md`) documents the 5 silent failure
gates; the skill is the authoritative entry point.

## 9. Current state snapshot

- Parent repo `feat-decoder-bringup`, HEAD ~`6e76316`. Submodule `core/MiSTer_MPEG2` HEAD `11d1aa2`
  with the recovery + counter-probe **uncommitted** in the working tree (also staged on dell,
  byte-identical). `core/mpeg2fpga` also dirty.
- dell `output_files/`: `mpeg2fpga_dvd_getbits.rbf` (known-good), `_seed5.rbf`/`_probe.rbf`/
  `_rebaseline.rbf` (this session's failures), + ~23 historical tags. Current `mpeg2fpga.sof` = the
  SEED-5 build; `mpeg2fpga.qsf` has `SEED 5` pinned (reset/remove it before Option A).
- Board `mister`/`ss1` (SuperStation) is the test target; de10 is the JTAG/SignalTap bench.
