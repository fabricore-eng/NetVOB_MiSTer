# crediq HW gate — 2026-07-03 (ramstyle credit-queue build)

Build: credit-queue shim + `ramstyle=logic` (md5 c93e92b0), rc=0, timing MET all corners
(general[1]/108MHz worst setup +0.565ns). rbf 862b7e31 flashed to the SuperStation.

## 1. THE STALL IS FIXED ON SILICON (unambiguous)
UART telemetry (`uart.txt`) shows a healthy, free-running decoder — NOT the old freeze:
- `U:0` every sample (no zero-fill / recovery events; the wedge signature was `M:D U:1`)
- `FC` (frame count) advancing 0258 → 0294 → 02D0 → 030C (frames produced continuously)
- `RP` tracks `P` (e.g. RP:4925 == P:4925); `J:0F3B == Z:0F3B` (whole stream consumed, loops)
- Framestore: all 4 slots hold full real frames (mean 126, nonzero 100%, stddev 56)

The weeks-long slice-2 stall is gone. The credit-queue (loss-proof command path) + ramstyle
(queue in ALM regs, not M10K) build decodes past the old death point on real hardware.

## 2. Gate VERDICT=FAIL (SSIM 0.9206 < 0.95) — but it's the animation-PHASE floor, NOT a fault
`hw_decode_verify.sh` auto-picked FRAME_2 and scored 0.9206 vs the best of ref_frame_01..06.
Objective proof this is phase, not a decode error:

- **Ref-vs-ref floor:** ref01-vs-ref02 (two PERFECT sim frames) = **0.9237 / 7.17%diff**.
  HW-vs-ref01 = **0.9206 / 7.07%** — statistically identical. The HW frame is as close to the
  reference as two consecutive perfect sim frames are to each other.
- **Region signatures match** (HW-vs-ref ≈ ref-vs-ref, the animation's own motion):
  TOP mad 6.87 vs 6.58 · MID 4.85 vs 4.63 · BOT 1.67 vs 1.58. The static BOTTOM band is
  near-bit-perfect (0.9918) — a real decode error would corrupt it too; it doesn't.
- **4-slot × 6-ref matrix** is uniformly 0.916–0.921 with no aligned pair: testsrc2 free-runs
  (~780 frames of a looping clip decoded), so framestore slots are arbitrary-phase.
- The **0.95 bar sits BELOW the ref-vs-ref floor (0.9237)** ⇒ unachievable for ANY
  non-phase-aligned frame, even a flawless decode.

## 3. Conclusion + next
Decode is correct and the stall is fixed, but there is **no objective ≥0.95 number yet** — the
milestone can't be formally claimed until the gate is phase-robust. Fix: re-gate with a STATIC
clip (freeze one frame, encode all-identical frames) so the free-running decoder always produces
the same content and compares to a single reference phase-free (expect ≥0.95 if decode is
correct). Clip+ref prep is FPGA-independent; the re-gate needs a board go.

Artifacts: `gate.txt` (verdict), `uart.txt` (telemetry), `hw_frame2_Y.png` (rendered HW frame).
