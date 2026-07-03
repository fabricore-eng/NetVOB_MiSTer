# Handoff — dvd — 2026-07-03 22:20
Branch: feat-decoder-bringup   ·   Repo: ~/Dev/fabricore/NetVOB_MiSTer

## TL;DR
**The decode-correctness milestone is MET on hardware** — the FPGA decodes a DVD-like
MPEG-2 stream correctly on silicon: HW gate VERDICT=PASS, frame-0 SSIM **0.9918 ≥ 0.95**
vs the golden reference (phase-free static clip). The weeks-long slice-2 stall is fixed.
Everything is committed + pushed (HEAD 4a386ab). Next rung is the parked **display-ack
wedge** (scanout), which does NOT block decode.

## State of play
- **Done (verified this session):**
  - **Timing fix (the session's real bug).** The handed-off "rc=0, ready to gate" build
    (credit-queue shim 3d8e036d) actually had NEGATIVE setup: -0.852/-1.257ns on
    `general[1]` (the 108MHz f2sdram/mem clock; `general[0]`=27MHz decoder), all other
    clocks positive. `report_timing` pinned it: Quartus inferred the 4-deep `q_addr`/`q_dta`
    credit-queue arrays as **altsyncram/M10K block RAM** in a far corner (X41_Y69) →
    -2.012ns clock skew + ~1.3ns RAM→raw_collides routing. FIX = `(* ramstyle = "logic" *)`
    on q_cmd/q_addr/q_dta → ALM regs + LUT mux → **general[1] setup +0.565 worst, timing MET
    all corners**. Synthesis-only ⇒ Verilator ignores it ⇒ sim byte-identical (frame0
    7121664c). Rebuilt rc=0, shim md5 **c93e92b0**. (commits 4ddf12b, f91282d)
  - **HW gate #1 (crediq, animated clip): STALL FIXED on silicon.** Decoder runs healthy —
    UART U:0 (no zero-fill recovery), FC advancing 0258→030C, RP tracks P, J==Z, all 4
    framestore slots full real frames. Gate scored 0.9206 = the **animation-phase floor**
    (proven objectively: ref01-vs-ref02 two-perfect-sim-frames = 0.9237, HW-vs-ref01 =
    0.9206, region signatures identical, static bottom band 0.9918). A free-running decoder
    of an animated clip never captures a phase-aligned frame → 0.95 unreachable.
  - **Static-clip methodology (removes the phase floor).** `make_test480i.sh` gained a
    `STATIC=1` mode (freeze testsrc2 frame-0, all-identical all-intra frames);
    `hw_flash_and_gate.sh` honors `GATE_CLIP`. Validated: every frame identical (SSIM 1.0),
    frame-0 == original clip frame-0 at 0.9990 (ffmpeg, FPGA-independent). (commit f81f6ec)
  - **HW gate #2 (staticgate, static clip): VERDICT=PASS.** frame-0 SSIM **0.9918** vs golden
    ref_frame_01 (%diff 0.111; need ≥0.95/≤2.0), bars 0.9933. Clean phase-lock: matches ONLY
    ref_frame_01 (frame-0) at 0.9918 while refs 02-06 sit ~0.92. Milestone met. (commit
    4a386ab; recorded in group chat verify=PASS; artifacts tools/hw_gate_runs/staticgate_20260703_PASS/)
- **In progress:** nothing half-built. The milestone is complete and checkpointed.
- **Blocked:** nothing.

## Key decisions (and why)
- **ramstyle=logic, NOT a pipeline register.** A 4-deep queue read combinationally at the
  head belongs in ALM regs + a LUT mux, not block RAM. It's synthesis-only, so sim stays
  byte-identical — no re-validation risk, no functional change. Do NOT let the queue arrays
  go back to RAM (they will silently at 108MHz and re-break setup).
- **Static clip for the correctness gate.** A free-running decoder + an ANIMATED reference
  caps SSIM at the ~0.92 phase floor (ref-vs-ref is only 0.9237, BELOW the 0.95 bar). Use
  `STATIC=1` clips (+ `GATE_CLIP`) for any phase-free correctness verdict. Don't re-gate the
  animated clip expecting ≥0.95 — it can't, even with a perfect decode.
- **Submodule edits tracked as patches, not gitlink commits.** `core/MiSTer_MPEG2` (+
  `core/mpeg2fpga`) show as dirty submodules — that's the expected working-tree state; the
  shim change lives in `core/patches/hw/mpeg2fpga-memshim-credit-queue.patch` (regenerated
  this session, base 33f3c2a→293870c). Do NOT `git add` the submodule gitlinks. Build via the
  no-ref hub launcher after scp'ing the shim to dell's working tree (dell-build-mechanics).

## Next steps
1. **(NEXT RUNG) Display-ack wedge** — the parked scanout freeze. Under any timing shift the
   raster-side disp consumer stops draining → resample_addrgen stuck STATE_WAIT →
   output_frame_valid never acked → picbuf→motcomp→vld freeze at ~frame 4. It does NOT block
   the decode gate (settled frames 0-2 land first) but blocks continuous playback/scanout.
   Fix direction (underrun-robust vsync resync of the disp addr/data pairing) is in
   `docs/HANDOFF-2026-07-02-stall-forensics.md` §FINAL. Any candidate fix must NOT stall the
   bus and must sim-validate byte-identical (word audit + trajectory trace + per-slot extractor).
2. **Scanout / interlace** — the OSD-squish video-timing bug (decode-independent; see
   [[scanout-blind-spot-ddr-vs-crt]] memory). Triangulate via the Frank menu test + HDMI OSD grab.
3. **Audio, A/V sync, PS→ES demux** (real DVD VOBs are program streams; current ingest is ES).

Verify (milestone already done): `tools/hw_gate_runs/staticgate_20260703_PASS/gate.txt` shows
`VERDICT=PASS` / SSIM 0.9918. Next-rung success = the CRT shows continuous frames past ~frame 4.

## Landmarks
- `core/MiSTer_MPEG2/rtl/mem_shim.sv:236` — credit-queue arrays with the `ramstyle=logic`
  attribute (shim md5 c93e92b0); RAW guard `ring_dist()` ~line 216; credit pull policy at the
  bottom of the command always-block.
- `core/patches/hw/mpeg2fpga-memshim-credit-queue.patch` — the shim as a re-appliable patch
  (what git tracks; submodule is non-pushable).
- `tools/testclips/make_test480i.sh` — `STATIC=1` builds the phase-free clip.
- `tools/build/hw_flash_and_gate.sh:25` — `GATE_CLIP` override; turnkey board flow
  (sof→rbf → devlock → warm-reboot → .mgl load → framestore dump → SSIM).
- `tools/hw_gate_runs/staticgate_20260703_PASS/` — PASS artifacts (gate.txt, uart.txt, frame).
- `docs/progress.md` #7-9 — this session's detailed trail.
- `docs/HANDOFF-2026-07-02-stall-forensics.md` §FINAL — display-ack wedge fix direction.

## Open questions / risks
- **Never let the queue arrays re-infer as RAM.** If the ramstyle attribute is dropped or the
  arrays are refactored, Quartus will map them to M10K again and general[1] setup goes negative
  (silent — only STA catches it). Always check the full per-corner setup list, not one corner.
- The staticgate dump had slots 2,3 empty (mean 0); harmless (slot 0/1 held the decoded frame,
  gate auto-picked slot 1). Note the framestore rotation if a future gate needs all 4 settled.
- Build box state: no build running. dell working-tree shim = c93e92b0 (ramstyle). rbf on dell:
  `output_files/mpeg2fpga_dvd_staticgate.rbf` (= crediq, same bitstream). Board (mister) released,
  running menu.rbf.
- Display-ack wedge onset is timing-dependent; treat every new hold/delay as suspect against the
  pulse/count-aligned-handshake fragility class (3 instances found; this 2007 design is riddled).
