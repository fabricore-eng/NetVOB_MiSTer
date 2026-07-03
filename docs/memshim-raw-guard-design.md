# mem_shim RAW guard — design (2026-07-02)

## Problem (offline-reproduced; tools/hw_gate_runs/*, run_ts2_raw32)
The f2sdram accepts writes posted: a READ accepted shortly after a WRITE to the same
address can be served from the pre-write state (bridge-internal read/write path skew).
mem_shim issues strictly in-order but has NO defense against this bridge-level RAW
hazard. The one victim that matters is the VBUF ring: the VLD's bitstream reads chase
the stream writer's head; a stale head-chase read silently desyncs the VLD (no error
flag) → phantom decode → consumes the stream to EOF writing nothing = the HW stall.
Sim dose-response: posted window ≥32 cycles kills decode; ≤8 is safe; the real HW
window is unknown and boot/timing-varying → the fix must be window-size-agnostic.

## Design: same-address recent-write guard (vbuf-window scoped)
Track the last K=4 writes to the VBUF window (word addr 22'h1C0000..22'h1EFFFE) in a
tiny shift-register CAM: {addr[21:0], age counter}. In S_IDLE, when the next command
is a READ whose address matches any entry with age < GUARD_AGE (param, default 64),
HOLD the read exactly like the existing read_throttled idiom: mem_req_rd_en=0, do not
pull the FIFO, retry next cycle. The read issues once the youngest matching entry ages
out. Writes and non-colliding reads flow unchanged.

### Why this shape
- **No global stall** (gap-fix lesson: never hold the whole bus): only the one
  colliding read waits, ≤GUARD_AGE cycles; command gaps of this size are routine
  (read_throttled already produces them) and SignalTap refuted the gap-wedge theory.
- **Never-mask doctrine**: the read is delayed, never dropped/faked; data returned is
  always the bridge's real response.
- **K=4 suffices**: the hazard is a sequential head-chase — the colliding read targets
  the most recent ring writes. (Review question: prove or bump.)
- **VBUF-only scope**: fwd/bwd motion-comp reads target the previous frame's recon
  (written far outside any posted window); display reads of a just-written recon row
  could take a stale word → at worst a transient pixel artifact, not a stall. Scoping
  keeps the CAM compare narrow (address top bits 111xx… pattern + 4 entries) to
  respect the +0.040ns worst-setup margin. (Review question: is display/recon residual
  risk acceptable for the decode gate? It does not affect the framestore dump verdict.)
- **GUARD_AGE=64 default**: > the 32-cycle kill threshold from the oracle with margin;
  parameterized for HW calibration. Cost when colliding: ≤64 idle cycles at 108MHz
  (~0.6µs) per collision.

## Validation ladder (all offline, before any build)
1. **No-hazard equivalence**: knobs-off, BOTH clips (greyramp + testsrc2), wp=0 and
   wp=2 → per-slot Y (extract_framestore_slots.py) byte-identical to baseline.
   (Whole-file ppm md5 is invalid: the guard shifts timing, dump-instant skew.)
2. **Hazard immunity**: +ddr_wr_commit_delay=32 (and 128) on both clips → decode must
   now complete with per-slot Y identical to baseline; RAW-STALE count must be 0
   (guard held every colliding read past the window).
3. **Composite realism**: +ddr_wr_commit_delay=32 +stream_gap=13 → same.
4. Regression: the age-gate/write-gate arms (drop_then_write_wedge model) still pass.
Then: HW build + decode gate (human's go), expect W to climb past ~2.29M with frames
completing; GUARD_AGE recalibration on HW if the stall shifts rather than dies.

## Rejected alternatives
- **Decoder-side fill gate (vbuf_fill≥N)**: already tried on HW (2026-06), stalled the
  decoder to all-zero and was reverted; wrong layer, and it can starve legal reads.
- **Full-address CAM (all windows)**: wider compare on the command path for hazards
  that cannot cause the stall; setup-margin risk for no decode-gate benefit.
- **Read-retry-on-collision (issue, detect, reissue)**: cannot detect staleness at the
  shim (data looks valid); would need bridge cooperation that does not exist.
- **2-deep skid / registered lookup**: adds a cycle to EVERY read to protect the rare
  colliding one; the knife-edge placement era that motivated register-everything ended
  with the SDC clock-groups fix.
