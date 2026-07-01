# HW decode gate — SDC clock-groups fix build — 2026-07-01 23:20 UTC

**Run:** `hw_flash_and_gate.sh sdcfix` on the SuperStation (devlock-gated, warm-rebooted
DOWN→UP before load, lock re-acquired post-reboot, released after). Build under test =
the SDC clock-groups fix (`227a471`, sof Jul 1 06:28, rbf md5 `4b31a85e…`).

## Verdict — a clean split

### 1. The f2sdram write-wedge is GONE on this build (single run so far)
Objective signals, all four UART samples (`uart.txt`):
- `U:0` and `PC:0000` throughout — waitrequest never stuck, no wedge/recovery flags.
- `RP` == `P` exactly in every sample — every issued read got a real response.
- Write counter `BN=0x22E5A5` (≈2.29M 64-bit writes) — sailed far past the framestore
  clear (the historical wedge froze W at ~33k *during* the clear). Reads kept climbing
  (`VN`≈30M+) for minutes after.
- `J == Z == 0xF3B` — the full 1.99MB test clip (3899 sectors) was pulled by the core.

The old wedge fired within seconds (during the clear), every boot, on wedging builds.
This run went clear → decode → full-clip ingest → sustained scanout with zero anomalies.
**Needs one repro run before claiming the milestone (protocol: never claim from a single run).**

### 2. FIRST real decoded pixels on hardware — then a stall ~2 slices in
The SSIM gate FAILs (0.0517 full-frame) but the failure *shape* is new information:
- Framestore was cleared to `0x80808080` (mid-gray YCbCr); 99.76% still holds that clear.
- Decoded content sits EXACTLY where the active `MP_AT_HL` memory map (`mem_codes.v:176`)
  puts frame 0: `FRAME_0_Y`@byte 0x0 (rows 0–31 only), `FRAME_0_CR`@0x200000 and
  `FRAME_0_CB`@0x280000 (each ≈¼ the luma volume — self-consistent 4:2:0), plus the first
  rows of `FRAME_1_Y`@0x300000.
- **Top 16 rows (slice 1) match `ref_frame_01.png` at SSIM 0.90 with identical mean
  (124.3 vs 124.3)** — recognizably correct picture, matching the *first* animation phase
  as expected. Rows 16–31 (slice 2) degrade (top-32 SSIM 0.58). No content below row 31.
- Writes then STOP (`BN` static across samples) while sector ingest continues to
  end-of-clip and display reads continue — the decode pipeline stalled, not the bridge.

### Interpretation (hypothesis, to be tested in sim — NOT confirmed)
Frame 0 is an I-frame: no motion-comp reference reads during its decode. The reads active
during frame-0 decode are the VLD's compressed-stream fetches from the VBUF ring
(`VBUF`@word 0x1C0000 = byte 0xE00000, just past this 14MB dump) racing the incoming
stream writes into the same ring through mem_shim. Prior art: the read-behind GAP /
"desync fix moves to the mem_shim layer" thread. A read-before-write hazard there feeds
the VLD stale bytes → desync mid-slice-2 → VLD consumes the rest of the stream without
finding sane pictures → pipeline idles ≈ exactly what we observe. Sim (idealized latency,
wp=0/2) decodes 3 full frames with the same RTL+shim → HW-only divergence → memory-
latency/ordering territory → build the sim latency oracle before any fix-swing build.

### 3. HDMI screenshot: all black (informational only)
Consistent with the scanout blind spot (DDR framestore ≠ CRT/HDMI path) and with a
99.7%-cleared framestore. No verdict weight.

## Files
- `uart.txt` — 4 telemetry samples (legend in HANDOFF-2026-07-01-sdc-clockgroups-fix.md)
- `gate.txt` — full SSIM gate output (VERDICT=FAIL, best ref SSIM 0.0517 full-frame)
- `fs_sdcfix.bin.gz` — the raw 14MB framestore dump (gzipped; 99.76% clear pattern)
- `hw_frame0_Y.png` — rendered frame-0 luma (the 32-row decoded band at top)
- `hdmi_osd_shot.png` — post-run HDMI screenshot (black)
