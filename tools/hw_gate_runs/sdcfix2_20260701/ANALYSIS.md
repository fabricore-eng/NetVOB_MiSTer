# HW decode gate REPRO run — same SDC-fix build, fresh boot — 2026-07-01 23:45 UTC

Repro of `../sdcfix_20260701/` (read that first). Same build (`227a471`, rbf md5
`4b31a85e…`), fresh warm-reboot + re-flash, human's per-run go.

## 1. Wedge-death REPRODUCED → milestone CONFIRMED (2/2 clean runs)
- `U:0` + `PC:0000` all four samples; `RP` tracks `P` (sample 3: RP=P−3 = 3 in-flight
  at the sampling instant — healthy pipelining, not a loss; it converged by sample 4).
- `BN=0x22E2CF` (~2.29M writes, within 0.03% of run 1), reads ~30M climbing, `J==Z`
  (full clip ingested).
- Two boots, two flashes, zero wedge signatures. The weeks-long f2sdram wedge — the
  project's central blocker — is **eliminated by the SDC clock-groups fix**.
  Ladder state: FEED✓ → read-return✓ → **bridge-wedge✓ (2026-07-01)** → decode
  stall/desync (CURRENT) → SSIM≥0.95 → scanout.

## 2. The decode stall reproduced — with timing-dependent extent (diagnostic!)
- Run 2 decoded band: again EXACTLY rows 0–31 of FRAME_0 (+ 4:2:0 chroma + first
  FRAME_1_Y rows), but SMALLER content volume (frame-0 post-bias mean 4.3 vs 6.1,
  nonzero 3.7% vs 5.7%; BN 726 writes fewer). The gate's auto-picker rejected the band
  ("no plausible frame") → VERDICT=FAIL, honest.
- **Slice 1 is near-bit-perfect this run: top-16-rows SSIM 0.9910/0.9870 (8/16 rows)
  vs ref_frame_01, mean 123.9 vs 124.3.** Run 1 was 0.90. Corruption onset: run 2 dies
  in rows ~16–23, run 1 held further into rows 24–31.
- Same build + same clip + different stall point across boots ⇒ the stall is
  **boot-to-boot timing/pacing dependent**, NOT a fixed bitstream/macroblock position.
  Rules out a deterministic VLD/table bug at a specific stream offset; points at the
  latency/pacing/starvation class.

## Constraints for the stall hypothesis (from the shim source, 4a8ca207)
- mem_shim is single-FSM, single in-order FIFO — no read-past-write bypass exists in
  the shim, and `PC:0000` (recovery_count=0) both runs ⇒ the resp_timeout zero-fill
  NEVER fired. So "stale VBUF read via shim reordering" is NOT the mechanism.
- Reads run only ~32k/s steady-state post-stall (P wraps ~2/s of 16-bit) — orders of
  magnitude below live-scanout needs; HDMI stayed black. Where display fetch pressure
  actually lands (through this path or not) needs mapping in emu.sv/mpeg2fpga mem_ctl.
- Sim baseline (same RTL+shim, lat30, wp=0/2) decodes 3 FULL frames byte-identically ⇒
  the HW-only divergence lives in what sim does NOT model: bursty/higher DDR latency,
  refresh gaps, active display fetch load, or real-time sd_* sector pacing.
  → Next: extend the sim latency oracle until the slice-2 stall REPRODUCES offline;
  no fix-swing builds before an offline repro.

## Files
`uart.txt`, `gate.txt` (auto-pick reject), `fs_sdcfix2.bin.gz`, `hw_frame0_Y.png`.
