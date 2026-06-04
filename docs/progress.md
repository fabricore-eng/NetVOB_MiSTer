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
- 2026-06-03 (cycle 3) — **hardware loop fully reachable — gating build/HW track UNBLOCKED**.
  Discovered + verified working SSH to all three via `~/.ssh/config`: build box **`dell`**
  (Ubuntu 26.04 x86; **Quartus Prime 17.0.2 Lite** in `raetro/quartus:17.0`, full
  map/fit/asm/sta), SuperStation **`mister`** (ARMv7 MiSTer Linux 5.15.1, `/dev/MiSTer_cmd`
  live), Pi 5 **`timepi`** (NAS `/mnt/nas/dvd-dumps` ready). Populated `.env` (hosts +
  key paths); `verify-session.sh` = **14 ok / 4 warn / 0 blocked** (only Plex/TMDB optional
  → stub/fallback). Confirmed `MiSTer_MPEG2` build target = **5CSEBA6U23I7** (`.qsf` + prior
  `build.log`), complete Quartus project + `sys/` → buildable as-is. blocked: nothing
  required. next (sequenced to avoid Dell contention): (1) on dump-done → move KUNGPOW
  Dell→Pi NAS, md5-verify both ends, delete Dell copy; (2) **first bitstream** — stage
  `core/MiSTer_MPEG2` to the Dell + **detached Quartus compile → .rbf**, then `load_core`
  on `mister` + **filmstrip** (M1a-on-hardware; treat the repo README's "NTSC video output"
  as UNVERIFIED until the filmstrip proves it — CLAUDE.md says no confirmed video yet);
  (3) fan out cycle-3 sim/service/ARM depth *during* the 30-min build (interleave, never idle).
- 2026-06-03 (cycle 4 — shared-hub integration) — **first `mpeg2fpga` `.rbf` built successfully**
  on the Dell (Full Compilation OK, ~30 min CPU, device 5CSEBA6U23I7) — M0 build milestone.
  Then **wired NetVOB into the shared MiSTer dev hub** (`~/Dev/mister-dev-hub`, mirrored
  `dell:~/mister-shared/`) so `dell` + `mister` are shared with the **573 session** without
  collision: read PROTOCOL.md + LESSONS.md; added the `dvd` row to `registry/projects.md`
  (pushed to the hub); added the shared-tooling/lock note to CLAUDE.md. **Retired my
  non-coordinated `tools/build/quartus-build.sh` + poll** (used `pgrep` — would match 573's
  Quartus + bypassed the shared lock; the hub's lesson is detect-by-`docker ps`-container-name)
  in favor of the hub's `tools/dell_build.sh` (serializes on `/tmp/dell-build.lock`, namespaces
  container `quartus-dvd`/log `dellbuild-dvd.log`) + `dell_coord.sh devlock` for test HW.
  Build config: `DELL_PROJECT=dvd DELL_TARGET=mpeg2fpga DELL_REPO=NetVOB_MiSTer/core/MiSTer_MPEG2`.
  blocked: none. next: `load_core` the `.rbf` on `mister` behind a `devlock` + filmstrip (M1a-HW),
  via the shared protocol. (cycle-3 local sim/real-data workflow still running — integrate next.)
