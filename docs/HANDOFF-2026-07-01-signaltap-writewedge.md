# HANDOFF — 2026-07-01 — SignalTap the f2sdram WRITE-path wedge

**Read this first, then [`docs/progress.md`](progress.md) (bottom entries) and the
session memories. This is the resume point for a fresh session.**

---

## TL;DR — where we are

The decode-on-HW blocker (the HPS **f2sdram bridge**) split into two problems this
session; **both are individually solved, but not yet in the same build**, and the
remaining obstacle is now a **write-path placement marginality**:

| Build (mem_shim md5) | Write placement | Gate logic | HW result |
|---|---|---|---|
| **hold-all write-gate** (`a74645df`) | **good** (cleared to `W:32994`, `U:0`, reads flowing) | **starves** (holds all writes) | no frame |
| **age-gate write-gate** (`4a8ca207`) | **bad** (wedges the clear, `U:1`, jitters `W:241..8511`) | **good** (sim-proven, byte-identical) | no frame |

- **HEADLINE WIN:** the write-gate **eliminated the weeks-long f2sdram bridge wedge**
  on silicon (`W` reached 32994 vs the historical stuck 85/189; `U:0`, `M:0`,
  `PC:0000`, `P`/`RP` churning; watchdog silent).
- The write-gate's over-aggression (write **starvation** on a dense read stream) was
  reproduced offline and **fixed by the age-gate** (hold writes only when a read is
  genuinely *stuck*, `resp_timer >= 128`) — **sim-proven: no starvation + decode
  byte-identical** to baseline AND to the greyramp golden.
- BUT the age-gate build **re-rolled the fitter into a bad WRITE-path placement**: it
  wedges during the framestore **clear** (pure writes, `P:0000`, `U:1` stuck),
  confirmed on **two clean boots** with a **jittering** wedge point (241 then 8511) =
  a marginal **physical** issue, not logic. Option A already made the READ-return
  placement-independent; the **write-command/waitrequest boundary is still
  combinational** → at the mercy of the fitter.

## The current step: SignalTap-first (chosen by the human)

We are probing the write-wedge on the **de10** JTAG bench to identify **which path**
is marginal, so we pick the right fix instead of guessing:

- **command-OUTPUT** marginal → a **simpler output-only register** suffices.
- **waitrequest-INPUT** marginal → the **full 2-deep skid buffer** is genuinely needed.
- **bridge-side** (FIFO-full / DDR-drain, correlated with `wr_count`/refresh) →
  **registration won't help; only placement / back-annotation will.**

### An instrumented build is IN FLIGHT (as of this handoff)
- Launched on dell (pid was `1355757`, container `quartus-dvd`, alongside 573's build).
- Log: `dell:/tmp/dellbuild-dvd.log`. Check done: `ssh dell 'docker ps | grep quartus-dvd'`
  (gone = done) then `grep "Full Compilation was successful\|== dell build DONE" /tmp/dellbuild-dvd.log`.
- It builds **mem_shim = age-gate + `st_waitreq` observation reg** (md5 `47ecf5fb`), with
  the SignalTap probe inserted (`quartus_stp --enable` already ran, 0 errors — the
  `.stp` shape-check PASSED; qsf has `USE_SIGNALTAP_FILE write_wedge.stp`).

---

## NEXT STEPS (in order)

1. **When the build finishes:** convert `.sof`→`.rbf` is NOT needed for SignalTap —
   SignalTap loads the `.sof` over JTAG. Confirm 0 errors + that the `auto_signaltap`
   instance is present (grep the fit log).
2. **HEISENBUG CHECK (critical):** the instrumented build MUST still wedge to be
   useful. Flash it and confirm the write-wedge reproduces (`U:1`, `wr_count` frozen,
   `P:0000`). If a good placement re-rolled (no wedge), the capture is empty — rebuild
   with a nudge (ballast) or accept the good placement as a lucky win and retest the
   age-gate for a frame.
3. **Capture on de10** (now free — 573 released it 2026-07-01 ~04:02Z):
   - `~/Dev/fabricore/tools/tools/dell_coord.sh devlock de10 acquire dvd`
   - Follow **`~/Dev/System573_MiSTer/tools/signaltap_573/RUNBOOK.md`** (the
     silicon-proven recipe; five silent-failure gates + the CSV-export trap).
   - JTAG runs in the dell docker: `ssh dell docker run --rm --privileged
     -v /dev/bus/usb:/dev/bus/usb --name jtag-dvd raetro/quartus:17.0 jtagconfig` etc.
   - Timed-arm: use a `.mgl` mount delay so you can arm the analyzer during the idle
     window BEFORE the bitstream/clear starts the wedge (the 2026-06-25 read-wedge
     capture used a 20s delay).
   - Decode the CSV with the runbook's **`read_stp_csv.py`** (raw Quartus CSV export
     DROPS the trigger channel's data column — do NOT trust raw exports).
   - Release the de10 devlock immediately after.
4. **Read the verdict** (see the decision tree below) → commit to the fix.

### What the capture answers (decision tree)
- `st_waitreq` (raw `ddr3_waitrequest`) **toggles then sticks**, correlated with a
  `wr_count` value or a periodic gap → **bridge-side** (FIFO/refresh). Registration
  won't help → **back-annotate the hold-all's good write placement** (option c below)
  or freeze placement.
- `st_waitreq` **sticks the instant a specific write is presented**, with `ram_write=1
  state=1` clean (a valid command) and `ram_read=0 outstanding=0` → the shim's command
  is clean but the bridge/capture drops it → a **boundary register** helps. Then:
  - if it looks like a **command mis-capture** → the simpler **output-only register**
    (register the command outputs via a combinational-ready skid — LOWER risk).
  - if it implicates the **waitrequest read** → the **2-deep skid buffer**.

---

## The fix options (re-scope after the 1-deep slice failed)

- **(a) 2-deep skid buffer** (fully-registered Avalon pipeline bridge) — the correct
  durable both-direction register. HIGH-risk RTL: an over/under-issued write corrupts
  data. A **1-deep slice is provably insufficient** (see below).
- **(b) output-only register** (register command outputs via a combinational-ready
  skid; keep waitrequest raw) — simpler, LOWER risk, adds output hold margin only.
  Bets the marginal path is the command output.
- **(c) back-annotate** the hold-all build's **known-good write placement** + graft the
  age-gate logic — sidesteps the RTL; brittle to the logic delta; needs the hold-all
  compiler DB (CDB) still on dell.
- **(d) SignalTap** — what we're doing now, to pick between (a)/(b)/(c).

### Why a 1-deep register slice is INSUFFICIENT (proven in sim this session)
Registering the command/waitrequest handshake in BOTH directions needs a **2-deep
skid buffer**. A 1-deep slice cannot be exactly-once against both slave behaviors:
- At `+ddr_wait_period=2` (stalling slave) it **DROPS the write**: `slice_accept`
  fires off a **stale** registered waitrequest (still reflecting the cycle BEFORE the
  command hit the pin) → retires a command the bridge never accepted → 0 frames.
- The fix for that (gate on "presented ≥1 cyc") re-introduces **double-issue** at
  `+ddr_wait_period=0` (never-stall: pin held 2 cyc, both waitrequest=0 → 2 accepts).
This is almost certainly the class of bug that sank the 2026-06-05 command-register
attempt. (Option A was easy only because a RESPONSE channel has no handshake.)
The buggy 1-deep slice is saved at `scratchpad/mem_shim.slice.sv` (do NOT ship it).

---

## Artifacts & state

### Git (branch `feat-decoder-bringup`, all pushed)
| Commit | What |
|---|---|
| `c88c2d9` | write-gate + sim-proof |
| `5d0a788` | write-gate HW gate — **bridge wedge ELIMINATED** (W 189→33k) |
| `ac6187e` | age-gate refinement (fixes starvation) |
| `6221658` | age-gate HW gate — write-path placement wedge found |
| `ef308e6` | durable write-register is a 2-deep-skid problem |
| `dc271cc` | write-wedge SignalTap probe (generator + qsf) |

### mem_shim.sv versions (the submodule is non-pushable; edits are patches / scp-to-dell)
- `4de9dcd1` = baseline (Option A read-return register + resp_timeout recovery). Reconstruct:
  `git checkout HEAD -- rtl/mem_shim.sv && git apply --include=rtl/mem_shim.sv <scratch>/memshim_baseline_pre_writegate.diff`.
- `a74645df` = hold-all write-gate. Patch: `core/patches/hw/mpeg2fpga-memshim-write-gate.patch`
  reconstructs the AGE-GATE now (`4a8ca207`), NOT the hold-all — the patch was updated.
  hold-all is at `scratchpad/mem_shim.writegate.sv`.
- `4a8ca207` = **age-gate** (the sim-proven logic; the canonical shim to keep). Also at
  `scratchpad/mem_shim.agegate.sv`. `core/patches/hw/mpeg2fpga-memshim-write-gate.patch`
  = baseline + patch → this.
- `abe57dd6` = buggy 1-deep slice (`scratchpad/mem_shim.slice.sv`) — reference only.
- `47ecf5fb` = age-gate + `st_waitreq` instrumentation = **current working tree + what's
  building on dell**. After the capture, **revert to `4a8ca207`** (`cp
  scratchpad/mem_shim.agegate.sv rtl/mem_shim.sv`) — st_waitreq is instrumentation only.

Scratch dir (session-specific, may not persist across app restart — copy anything you
need into the repo): `/private/tmp/claude-501/-Users-human-Dev-fabricore-NetVOB-MiSTer/d065139d-fe68-4bbb-8bb7-9c329eba87a2/scratchpad`
(`mem_shim.{baseline,writegate,agegate,slice}.sv`, `memshim_baseline_pre_writegate.diff`,
`hwgate_*.log`).

### SignalTap probe (committed, in `tools/signaltap/`)
- `write_wedge_stp.tcl` — generator (adapt NODES/trigger, regenerate `.stp`; `.stp` is gitignored).
- `write_wedge.qsf.snippet` — PRESERVE_REGISTER list (34 taps).
- Taps: `ram_write ram_read state st_waitreq wedged wr_count[15:0] wr_stall_cnt[8:0]
  outstanding_reads[2:0]`; trigger on `wedged`, post-position, depth 8192 @clk_mem.
- Prior read-wedge probe (2026-06-25): `tools/signaltap/ddram_wedge_*` (reference).

### dell state to clean up after the capture
- `dell:~/NetVOB_MiSTer/core/MiSTer_MPEG2/mpeg2fpga.qsf` is **signaltap-enabled** (dirty).
  Clean backup at `mpeg2fpga.qsf.clean_bak` on dell — restore it for future clean builds.
- dell's `rtl/mem_shim.sv` = `47ecf5fb` (instrumented). Re-stage `4a8ca207` for the fix build.

## Sim oracle (`core/sim/memshim`) — how to validate any fix offline
- Build: `rm -rf obj_dir && make build` (obj_dir caches an old abs path — always rm it
  after a rename/first build). Uses the working-tree `mem_shim.sv` as the UUT.
- Stream: `core/sim/prep_stream.sh <SRC.m2v> 4194304 core/sim/memshim/stream.dat`
  (greyramp = `core/mpeg2fpga/tools/streams/greyramp.mpg`; the dense HW clip =
  `tools/testclips/test480i_ntsc.m2v`).
- Run: `./run_memshim.sh <RUNDIR> <MIN_FRAMES=3> <MAX_WAIT> -- +ddr_rd_latency=30
  +ddr_wait_period=2 [+ddr_drop=N +ddr_drop_then_write_wedge=1]`.
- Starvation reproduces at **latency ≥30** (latency 8 hides it); byte-identical decode
  vs baseline is the correctness gate; `wr_issued` vs decoder-write-count is the
  exactly-once gate (note `wr_count`==`wr_issued` trivially — compare vs BASELINE run).
- **Validate a command register slice at BOTH `+ddr_wait_period=0` AND `=2`** — the
  1-deep slice passed wp=0 but DROPPED writes at wp=2.

## TOOLING TODO (bit us this session)
- `tools/build/hw_flash_and_gate.sh` has a **reboot-detection RACE**: the up-check fires
  in the window after the reboot command but before the board drops → the re-acquire
  hits it mid-reboot and aborts. FIX: verify the board goes DOWN (ssh fails) *before*
  polling for UP (a rebooting host **fast-fails** ssh, so a naive poll burns iterations
  in seconds — pace with `sleep`, not just `ConnectTimeout`). Manual-gate workaround
  used this session: reboot via `dell_coord devlock <board> reboot dvd`, wait DOWN→UP,
  re-acquire, `load_core` the pre-staged `.mgl`, read UART, dump, verify.

## Key facts / gotchas (durable)
- **Warm-reboot before ANY verdict** (session non-negotiable) — `load_core` leaves the
  HPS f2sdram bridge in the prior state; only a reboot clears a wedge.
- **No capture card** — the decode verdict = DDR framestore dump (`/dev/mem`
  0x30000000) vs `core/sim/artifacts/decode_ref_set/`, not the CRT/HDMI. Beware the
  flat-field SSIM≈0.70 auto-pick false-positive.
- UART legend (115200 8N1, `/dev/ttyS1`): `W`=writes `P`=reads `RP`=responses
  `U`=waitrequest `M`=shim_state `PC`={wedged,lock_cmd,lock_outstanding,recovery_count}
  `Z`=total_sectors `J`=next_lba `FC`=frame_cnt. Wedge = `U:1` + `W`/`P` frozen + `PC>=0x8000`.
- `mister`/`ss1` = SuperStation (decode gate); `de10` = DE10-Nano JTAG bench (SignalTap).
  Each has its OWN devlock; mister is shared with the 573 session — coordinate via
  `dell_coord.sh chat`, gate every disruptive action on a SUCCESSFUL devlock acquire.
- HW gate needs the human's **explicit in-session "go"** each time (the auto-mode
  classifier blocks it otherwise; "the word" was flagged ambiguous once).
