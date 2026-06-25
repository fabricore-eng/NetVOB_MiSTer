# HANDOFF — next step: non-stalling read-pacing + correctness-preserving re-issue recovery

> **⚠️ SUPERSEDED 2026-06-25 by the SignalTap capture.** The "non-stalling cap / pacing" framing below was
> based on the INFERRED throttle-hold theory, which SignalTap on de10 **REFUTED**. The real wedge is a
> **dropped read response → write-block** (outstanding only ever =1; the throttle never engaged). The current
> truth + the corrected fix (gate WRITES on `outstanding_reads==0` + a short re-sync; and/or reduce the
> residual read-response drop) are in **[`docs/mem-shim-design-analysis.md`](mem-shim-design-analysis.md)**
> (Recommended path §0a/0b) and the newest **[`docs/progress.md`](progress.md)** entry. The §4–§6 *mechanics*
> below (build / flash / manual gate / SignalTap recipe) are still accurate and reusable — only the §1–§4 *fix
> theory* is stale.

**Date:** 2026-06-25 · **Branch:** `feat-decoder-bringup` · **Session key:** `dvd`
**Supersedes:** `docs/HANDOFF-2026-06-24-placement-fix.md` (Option A from that handoff is DONE — see §2).
**Read first (CORRECTED):** `docs/mem-shim-design-analysis.md` + newest `docs/progress.md` entry + memory
`bridge-placement-marginal-root-cause` (#3 update). This doc's mechanics sections remain useful.

---

## 1. One-paragraph state

The MPEG-2 decode logic is correct (sim-proven). The blocker is the **HPS f2sdram memory bridge wedging**
before a frame is produced. This session built a **complete, silicon-validated model** of *why* it wedges
(§3) and **proved Option A** (registering the read-return boundary) fixes the dead-read-return sub-problem.
The remaining work is a **mem_shim redesign**: pace reads without stalling the pipeline + recover lost reads
by **re-issuing** them (not holding, not zero-filling). Design + sim-validate first (like Option A), then build.

## 2. What's DONE and banked (don't redo)

- **Option A — register the read-return boundary — IMPLEMENTED, SIM-VALIDATED, PROVEN ON HW.** In
  `core/MiSTer_MPEG2/rtl/mem_shim.sv`, `ddr3_readdatavalid`/`ddr3_readdata` are registered once at the input
  (`rdv_q`/`rdd_q`) and all consumers driven from them. Sim: decoded frame byte-identical (timing-only). HW:
  raw `VL` (DDRAM_DOUT_READY pulses) went **0 → ~84** (reproduced) — the dead-read-return is fixed. It is
  **canonical** (local working tree + dell, md5 `4de9dcd1`). Patch:
  `core/patches/hw/mpeg2fpga-memshim-readreturn-register.patch`. **Keep it** — it's orthogonal to the wedge.
- qsf has **no SEED pin** (default placement) — keep it that way (Option A aims for placement-independence).
- Commits: `71f3197` (Option A + patch), `487092c` (HW gate results) on `feat-decoder-bringup` (pushed).

## 3. The wedge model — PROVEN on silicon this session (don't re-derive)

Three interlocking failure modes, all measured via the emu.sv uart counters (`VL`=raw DOUT_READY pulses,
`VN`=reads accepted, `BL`=BUSY cycles, `P/RP`=mem_shim rd/rsp, `PC`={wedged,lock_cmd,lock_outstanding,recovery_count}):

| build | reads before wedge | wedge on | in-flight at lock | zero-fills (recovery_count) |
|---|---|---|---|---|
| READ_LIMIT=4 (current) | ~85 | WRITE | 1 | 1 |
| READ_LIMIT=63 (throttle off) | ~6000 | READ | **9** | **9** |
| getbits (simple shim, control) | millions | none | — | — |

1. **Over-issue lock is REAL.** Unthrottled, the shim piles reads up to **9 in-flight** and the f2sdram LOCKS
   on a read. (Sim modeled lock@6; HW shows ~9.) So read-limiting IS needed.
2. **The current throttle (=4) is NET-HARMFUL.** It wedges **70× earlier** (~85 vs ~6000 reads). Its method —
   *hold the read, don't pull the FIFO* — stalls the pipeline → a different deadlock. This is the documented
   "stalling the bus deadlocks — the f2sdram needs the pipeline moving" hazard (mem_shim comment + RocketBoards
   "Cyclone V — can write but cannot read" + Avalon pending-reads spec).
3. **The zero-fill recovery CORRUPTS.** `resp_timeout` synthesizes ZERO data for a lost read; `recovery_count=9`
   means 9 reads were corrupted before the lock — the `never-mask-faults-with-fake-data` anti-pattern, live.

Both builds' framestores are empty (mean 0.1) → neither decodes a frame. getbits (no throttle, simpler/more-
serial FSM, no recovery) churns freely but desyncs when it loses a read.

## 4. The fix (design-first, sim-validate before any build)

Redesign mem_shim's read path to satisfy ALL THREE at once:
- **(a) Non-stalling in-flight cap.** Cap outstanding reads BELOW the ~9 HW lock **without gapping the
  pipeline**. Do NOT use the current "hold the FIFO read" method (it stalls → early wedge). Options: a
  credit/skid scheme that keeps issuing while ≤ cap; or study getbits's FSM (it naturally stays low-in-flight
  AND keeps moving — that's the existence proof). A first cheap experiment: `READ_LIMIT=8` (just below 9) — but
  expect the hold-method early-wedge to persist, so the real fix is changing the *method*, not the number.
- **(b) Correctness-preserving lost-read recovery.** When a read response is genuinely overdue, **RE-ISSUE the
  read** (you have its address/tag) instead of holding or zero-filling. Remove the zero-fill band-aid once the
  re-issue path works.
- **Validate in `core/sim/memshim` first** (the real mem_shim is the UUT). The model now has a `lock_threshold`
  knob — set it to ~9 to match HW. Confirm: decoded frame byte-identical, in-flight stays < lock, NO zero-fill
  fired, no stall. THEN build + gate.

## 5. Exact mechanics (verified this session)

- **Build (no ref ⇒ dell working tree):**
  `DELL_PROJECT=dvd DELL_TARGET=mpeg2fpga DELL_REPO=NetVOB_MiSTer/core/MiSTer_MPEG2 ~/Dev/fabricore/tools/tools/dell_build.sh`
  (~35 min detached; `--who` for slots; produces `output_files/mpeg2fpga.sof`).
- **Stage a local edit to dell** (submodule origin is upstream → CANNOT push): `scp` to `/tmp`, `diff` vs dell's
  working tree, `cp` in. Snapshot the prior file off-box first (e.g. `/tmp/mem_shim.optA.bak`).
- **Flash + gate — DO IT MANUALLY** (the turnkey `tools/build/hw_flash_and_gate.sh` trips the auto-classifier
  because its devlock acquire is *internal* and acquire isn't idempotent for the same holder). The manual flow
  that works (each disruptive step gated on a held devlock):
  1. `dell_coord.sh devlock mister acquire dvd` (explicit, visible) + verify status.
  2. sof→rbf: `ssh dell "cd <repo> && docker run --rm -v \$PWD:/work -w /work raetro/quartus:17.0 quartus_cpf -c -o bitstream_compression=on output_files/mpeg2fpga.sof output_files/<tag>.rbf"` (sane size ~2.9–3.0M).
  3. copy rbf dell→mister as `/media/fat/mpeg2fpga_dvd.rbf` (the .mgl's `<rbf>` target); md5-match both ends.
  4. **WARM-CYCLE:** `dell_coord.sh devlock mister reboot dvd`, wait for `/dev/MiSTer_cmd`, **verify uptime small
     (~30s) = genuine cycle** (the wait loop can false-positive before the board drops), then **re-acquire** (the
     cycle wipes the on-board lock).
  5. **re-copy `tools/build/dump_framestore.py` to mister:/tmp** (the warm-cycle WIPES /tmp).
  6. `load_core /media/fat/mpeg2_test.mgl` (the abspath .mgl already on board), wait ~12s, check CORENAME=MPEG2.
  7. uart: `ssh mister 'for d in /dev/ttyS1 /dev/ttyS0; do stty -F $d 115200 raw -echo; timeout 6 cat $d | tr -d "\r" | grep -m5 .; done'`.
  8. dump: `python3 /tmp/dump_framestore.py 0x30000000 0xE00000 > /tmp/fs.bin`; scp to Mac; `bash tools/build/hw_decode_verify.sh fs.bin --out-dir <d> --log-cmd` (PASS iff SSIM≥0.95 AND %diff≤2).
  9. cleanup: `load_core /media/fat/menu.rbf` + `dell_coord.sh devlock mister release dvd`.
- **Hook gotcha:** the PreToolUse hook blocks any command containing the literal `ssh <host> reboot` pattern —
  AND it false-matched the word "reboot" inside an echo on a raw `ssh mister '…'` line. Use `dell_coord.sh
  devlock mister reboot dvd` for cycling and keep the word "reboot" out of echoes on raw-ssh lines.

## 6. Counter-reading cheat-sheet (Option-A / counter-probe builds only)

`VL`=raw DOUT_READY pulses (read responses at the pin), `VN`=reads accepted at pin, `BL`=BUSY cycles (climbing
forever = wedged), `BN`=writes accepted, `P/RP`=mem_shim rd/rsp, `M`={cmd,saved_valid,state} (9=READ/WAIT,
D=WRITE/WAIT). `PC`=`{wedged(1),lock_cmd(2)={rd,wr},lock_outstanding(6),recovery_count(7)}`. Healthy = `VL`/`P`/
`RP` all climbing together; wedge = they FREEZE while `BL` climbs. **getbits predates this probe** — its VL/VN
carry the OLD VLD/coeff semantics; compare getbits only via `P/RP` churn + decode extent.

## 7. Don'ts

- Don't re-add the read-*hold* throttle method (proven to wedge early). Cap in-flight without stalling.
- Don't zero-fill a lost read (corrupts). Re-issue it.
- Don't LogicLock (license-blocked on Quartus 17.0 Lite). Don't push the submodule (upstream origin).
- Don't trust hw_decode_verify's Step-2 auto-pick (flat-field false-positive ~0.70) — trust the numeric VERDICT
  + the bridge counters + LOOK at the frame.
- Don't claim from one run — reproduce on a fresh warm-cycle.
