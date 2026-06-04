# NetVOB_MiSTer — progress log (the loop's checkpoint "memory")

Durable, human-readable trail for the unattended `/loop` run
([`autonomy.md`](autonomy.md) §0). Git history is the *primary* checkpoint; this is the
at-a-glance state a fresh context (or a human) reads to resume **without re-deriving it**.

- **Single-writer:** only the orchestrator (main thread) appends here — never a parallel
  agent ([`autonomy.md`](autonomy.md) §5).
- **One dated line per cycle.** Format:
  `YYYY-MM-DD — <advanced> | blocked: <…> | building: <…>`
- Keep it terse; the commit it accompanies holds the detail.

---

- 2026-06-03 — Planning phase complete (PLAN.md + docs + autonomy/bootstrap layer, incl.
  the §0 `/loop` kickoff pattern). Loop not yet started. First cycle must: run
  `scripts/verify-session.sh`, then post the consolidated resource request per
  `session-bootstrap.md` (SSH to mister/Pi, `.env`, DVD dumps, Quartus, Plex/TMDB) before
  any build/HW work. blocked: nothing yet | building: nothing.
- 2026-06-03 (cycle 1) — **M1a de-risk CLEARED IN SIM**: vendored `mpeg2fpga` +
  `MiSTer_MPEG2` as pinned submodules (all 6 upstream SHAs git-confirmed — supersedes the
  page-parse caveat); stood up `core/sim/` Verilator harness that decodes `greyramp.mpg`
  to a correct interlaced RGB frame PNG, **reproduced byte-identical from a pristine
  checkout** (md5 80eef456…), with the 3 RTL-vs-Verilator fixes captured as an
  auto-applied re-appliable patch. Also landed (off-target, all tests pass): `service/`
  provider (Source ABC + catalog + protocol + streamer + DVDDumpSource PS nav-strip + Plex
  stub, 43 unittests), `arm/` PS-demux (PS→video ES, audio-aware, PTS, 5 C tests),
  `tools/` harness (480i/480p MPEG-2 PS clip gen + filmstrip). Preflight = LOCAL Mac,
  sim-only (toolchain green; all `.env` hosts empty). blocked: hardware/build-box/Pi/DVD
  dumps + Plex/TMDB creds — **consolidated resource request posted to user** (user offered
  SuperStation+Pi+build-box+dumps+both creds; headless Dell/Ubuntu proposed as build box +
  dump host — awaiting `.env` values). building: nothing (all increments verified+committed).
  next: decoder-sim breadth (real-motion I/P/B clip via `tools/` clips→ES; NTSC 480i
  modeline; numeric PSNR-vs-ffmpeg check) — all sim-only, no hardware needed.
- 2026-06-03 (cycle 2) — **decoder-sim breadth**: real-motion **I/P/B decode CONFIRMED**
  (first motion-compensation test — `picture_coding_type` advances I→P→B, frames visibly
  move; filmstrip in `core/sim/artifacts/`); **NTSC 480i modeline added** (720×480i geometry
  verified live; analog field-rate/PLL = 13.5 MHz dotclock flagged for HW, not a sim
  blocker); numeric **Y-PSNR = 28.67 dB** vs an ffmpeg reference (below my 30 dB bar but
  root-caused as benign IDCT-precision divergence — bounded |Δ|≤17, high-freq only — not a
  decode bug). 3 RTL edits captured as auto-applied patches; harness **reproducible from a
  pristine submodule** (lint 0 errors, verified by orchestrator). **service**: real
  PS-over-TCP media server + JSON control channel + localhost-loopback E2E test
  (browse→play→pause→resume→seek→stop, byte-exact PS) — 52 tests (43+9), non-flaky / 80
  runs. **arm**: SPSC ring buffer + `sd_*` sector-pull backpressure model + audio-ES
  routing; 4 host-test binaries pass (ASan/UBSan clean). Also (interactive, hardware
  discovery): reached the **Dell build box** (Ubuntu 26.04 x86 — Docker + `raetro/quartus:17.0`
  ALREADY present → bitstream path essentially ready), confirmed **`timepi` = the Pi 5**;
  installed libdvdcss + ripped the user's **KUNGPOW** DVD (CSS), dumping to the Pi's 512 GB
  NAS `/mnt/nas/dvd-dumps/` as the first real DVDDumpSource fixture. blocked: `.env` still
  unfilled (Pi=`timepi`, build box=`dell` now known; SSH key paths + Plex/TMDB pending).
  building: KUNGPOW `dvdbackup` mirror on the Dell (→ NAS move on completion).
