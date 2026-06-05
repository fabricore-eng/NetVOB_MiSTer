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
- 2026-06-03 (cycle 3 results — landed during the build window, all reproduced) — **decoder-sim
  depth**: FIELD PARITY confirmed (correct top/bottom weave 22 dB vs swapped 3.8 dB = 18 dB gap;
  per-field 30.4 vs 3.9 dB) — field-exact order validated in sim; **I/P/B decoded through the
  NTSC 480i interlaced path**; GOP soak clean (21-GOP/240-frame clip, no leak/drift,
  wall-clock-bounded). PSNR honestly **stays 28.67 dB** — IDCT-flavor hypothesis **REFUTED**
  (ffmpeg int/simple/default near-identical); residual = the 2007 fixed-point datapath vs ffmpeg
  as a class (bounded max|Δ|=18, −2 LSB DC → `yuv2rgb.v` suspect), a conformant-decoder
  difference, not a bug. HW note: `syncgen` odd_field is phase-offset one field from BT.601
  content parity → the ADV7125 field-ID must lock to the *emitted* field (HW bring-up item).
  **DVDDumpSource lossless path VALIDATED ON REAL DVD DATA**: walked the full 30 MB KUNGPOW
  VTS_10_1 slice, 0 parse errors, stripped exactly 154 `0xBF` nav packets losslessly → ffprobe
  confirms valid MPEG-2 PS (mpeg2video 720×480 + 6× AC-3); real IFO parser (`ifo.py`) enumerates
  titles for `browse()`; 68 tests (skip-guarded). **ARM real pipeline** (ringbuf→demux→feeder) on
  the real slice: video ES **byte-identical to ffmpeg**, 0 FIFO/ring overruns, AC-3 substream
  histogram matches ffprobe's 6 tracks; insight: the audio sink's byte-runs can't recover
  substream-id without PES boundaries. **No parser bug exposed by real VOBUs.** blocked: none.
  next: `load_core` the `.rbf` on `mister` behind a `devlock` + filmstrip (M1a-HW).
- 2026-06-04 (cycle 5 — FIRST HARDWARE bring-up on the real SuperStation) — via the shared
  lock protocol: converted the `.sof`→ a **compressed `.rbf`** (`quartus_cpf -o
  bitstream_compression=on`, 2.97 MB; the sys/ `.sof→.rbf` POST_FLOW didn't fire on the
  build — **TODO wire it**), acquired the `dvd` devlock, `load_core`'d it → **`CORENAME`
  MENU→MPEG2, MiSTer main running our core**. **PROVEN: our build loads + runs on real HW
  and drives ANALOG video out** (the CRT over component shows output, not black) — first HW
  bring-up of NetVOB. Found the clip-load path = `CONF_STR "S0,MPG M2V"` (mount to drive
  slot 0 via the `sd_*` seam); fed the author's `stream-susi.mpg` via a **`.mgl` autoload**
  (load core + mount to S0). **RESULT: CRT shows a SCRAMBLED raster, UNCHANGED with vs
  without a clip → the blocker is ANALOG OUTPUT TIMING, not decode.** Root cause: `emu.sv`
  drives `VGA_*` straight from the mpeg2fpga decoder syncgen at a fixed **27 MHz `CLK_VIDEO`
  + `CE_PIXEL=1`** with a non-NTSC modeline → not a CRT-lockable NTSC raster. (MiSTer.ini
  analog cfg is correct: `vga_mode=ypbpr`, `forced_scandoubler=0`, menu syncs at 240p.)
  Key gotcha: this core drives the analog raster directly (not `FB_*`/scaler), so the MiSTer
  **`screenshot`/filmstrip is ALWAYS BLACK** for it → HW verification needs the CRT or an
  analog capture, not screenshots. blocked: none (HW reachable). next = the **M7 analog
  frontier, pulled forward**: make `emu.sv`/syncgen emit a CRT-lockable NTSC raster — target
  **240p first** (easiest sync; ~1716 total px/line @27 MHz or 858 @13.5 MHz via `CE_PIXEL`),
  then **480i** (sim NTSC 480i modeline + interlaced `VGA_VS` + 13.5 MHz dot clock). Iterative:
  rebuild (~30 min, shared lock) → CRT check (user-in-the-loop). devlock released.
- 2026-06-04 (cycle 6 — analog 480i fix, BUILDING) — fully root-caused the CRT scramble (with the
  573 session's debugging notes): `emu.sv` emitted the WRONG raster — the build defaulted to
  `MODELINE_NTSC` = **480p PROGRESSIVE @27 MHz** (a 480i YPbPr CRT can't lock), with `CE_PIXEL=1`
  (27 vs 13.5 MHz BT.601) + `VGA_F1=0` hardcoded + no `video_mixer`. Drafted + **adversarially
  verified** (workflow) an `emu.sv` rework: `MODELINE_NTSC_INTERL` (720×480i, 858-dot/13.5 MHz line,
  HALFLINE=428), `CE_PIXEL=ce_pix` (27/2=13.5 MHz), `VGA_F1` ← decoder field parity (`v_pos[0]`/
  `odd_field`, no decoder port change), `VGA_SCALER=0`, VGA_* re-registered on `ce_pix`. Verdict:
  sync math SOUND (15.734 kHz exact; proven mpeg2fpga interlace idiom; clean clock domains;
  lints/compiles) — "uncertain" only on `VGA_F1` field-ORDER (coin-flip → flip on HW if fields tear).
  Pre-build check passed (`.qsf` has no MODELINE macro → default = NTSC_INTERL). Patch =
  `core/patches/hw/mpeg2fpga-emu-ntsc480i.patch` (kept OUT of the sim `core/patches/*.patch` glob —
  it targets `core/MiSTer_MPEG2`, not `core/mpeg2fpga`). Building on the Dell via the shared lock
  (`quartus-dvd`). next: `cpf`→`.rbf` → load via `.mgl` behind the `mister` devlock → CRT test
  (confirm stable lock; check/flip F1). HW follow-ups: no NTSC equalizing pulses (some CRTs may roll),
  27 MHz not ×1000/1001 (0.1% fast), reconcile sim-vs-hw modeline totals (minus-one convention).
- 2026-06-04 (cycle 6 result — **ANALOG 480i TIMING FIXED on real HW** ✅; decode-on-HW is the
  remaining gate) — built + loaded the `emu.sv` NTSC 480i fix on the SuperStation: **CRT scramble →
  STABLE LOCKED raster** — the analog field-exact-480i frontier is CLEARED (the CRT now syncs to our
  core's output; `VGA_F1`/`ce_pix`/`MODELINE_NTSC_INTERL` fix confirmed in-system). With a **720×480
  NTSC color-bars clip** (and the 352×240 stream-susi) mounted via the `.mgl`, the screen is **stable
  BLACK** = no decoded pixels on the now-correct raster. Both clips black → systematic, not per-clip.
  This isolates the remaining gate = the project's **#1 RISK** (CLAUDE.md: "no confirmed video; FSM /
  mem_shim DDR3 hangs"): the decoder is **sim-proven on these exact streams** (bench behavioral RAM),
  so the suspect is the **HW-only datapath the sim never exercised** — `mpg_streamer` `sd_*` feed
  (CLAUDE.md: loading is verified) → **`mem_shim` DDR3/CMA framestore** → decoder FSM under real SDRAM
  latency → `VGA_*`. NEXT (the M1a gate, OFF-BOARD): a **full-PORT Verilator sim** (emu + decoder +
  `mem_shim` + a DDR3 model + mounted-image feed) to reproduce the black + localize the stall (feed?
  mem_shim? FSM? framestore read?), plus exposing decoder `vld_err`/FSM state to LEDs/HPS for on-HW
  confirmation. devlock released; `VGA_F1` field-order moot until there's a picture. **NET TODAY:
  decoder de-risked in sim; full toolchain + hub coordination; KUNGPOW dumped; first HW load; analog
  480i timing fixed on HW. Remaining: confirmed decoded video on HW (the gate).**
- 2026-06-04 (cycle 7 — OVERNIGHT, mem_shim sim repro = NEGATIVE/narrowing) — built a
  mem_shim-in-the-loop Verilator harness (`core/sim/memshim/`: decoder + the real `mem_shim.sv` + a
  behavioral f2sdram/DDR3 Avalon model with multi-cycle waitrequest + N-cycle `readdatavalid` +
  jitter; greyramp feed; clk_sys 27 / clk_mem 108). **RESULT: decode-through-`mem_shim` is CORRECT** —
  clean greyramp Y-ramp, byte-identical zero-latency vs harsh-but-**conformant** DDR3 (verified PNG).
  The HW black did NOT reproduce → refutes the conformant tag-desync/address hypotheses; narrows the
  bug to: (a) NON-conformant bridge behavior (reorder/drop/dup `readdatavalid`); (b) the concrete
  **ADDR_ERR same-cycle response collision** in `mem_shim.sv` (lines 109 vs 139/176 both write
  `mem_res_wr_en` → a real `readdatavalid` colliding with a synthetic ADDR_ERR drops the real response
  → tag-FIFO misroute → black; greyramp never hits ADDR_ERR so untested); or (c) the **fork's
  Xilinx-FIFO build** (sim used bench soft FIFOs). Also unresolved: is the black a DECODE failure or a
  FEED failure (mpg_streamer/mount delivering no bitstream)? — not yet distinguished; decisive test is
  HW debug counters / the fork's `uart_debug`. `mem_shim.sv` unedited (gitlink pinned). next (overnight,
  off-board): investigate the FIFO build + the mpg_streamer→decoder feed wiring; fix the ADDR_ERR
  collision regardless; extend the sim with non-conformant DDR3 modes to test desync; prep a morning
  HW diagnostic (read `uart_debug` rd/rsp/frame counts behind the devlock to localize feed-vs-decode-
  vs-mem) + a candidate `.rbf`.
- 2026-06-04 (cycle 8 — OVERNIGHT, decode-on-HW candidate fixes + morning plan) — investigated the
  fork's FIFO build + feed + ADDR_ERR. SIM-PROVEN FINDINGS: (1) Xilinx-FIFO worry WRONG — single-clock
  FIFOs use the soft `xfifo_sc` (= sim); the 3 DUAL-clock FIFOs use the fork's hand-written Gray-code
  `xilinx_fifo_dc`, which the sim never ran — BUT the decisive swap experiment proved it decodes
  **BIT-IDENTICAL** to the proven `generic_fifo_dc` (54 MB exact), so the Gray FIFO is **NOT a
  functional** black cause (only possible physical/timing). (2) The `mem_shim` **ADDR_ERR same-cycle
  response collision is a PROVEN data-corruption hazard** (directed `tb_addrerr.v`: unfixed shim
  overwrites real DDR3 data with synthetic 0); FIXED + sim-validated — but greyramp doesn't naturally
  trigger it, so it's a real defect, not confirmed as THE field black. (3) Non-conformant DDR3
  `+drop`/`+dup` reproduce a decode HANG (response desync); reorder/late-start don't. Candidate
  patches in `core/patches/hw/`: **`mpeg2fpga-memshim-addrerr-fix.patch` (LEAD)**,
  `mpeg2fpga-fifo-dualclock-use-generic-fifo-dc.patch` (demoted/backup). Morning HW-diagnostic plan:
  **`docs/hw-decode-diagnostic.md`** (read the fork's `uart_debug` RP/P/FC/T counts → decision tree:
  feed vs decoder/FIFO vs mem_shim vs video-out). OVERNIGHT BUILD: building the **480i + ADDR_ERR**
  candidate `.rbf` on the Dell (morning-ready); NOT loaded on mister (off-board constraint). next
  (morning, with user): load the candidate → read `uart_debug` to localize → CRT-test; if still black,
  the decision tree picks the next patch (FIFO swap / feed-latch hardening).
- 2026-06-04 (cycle 9 — OVERNIGHT spine, off-board while the gate build is blocked) — deepened
  DVDDumpSource toward **M3**: full **PGC parser** (`ifo.py parse_pgc`: PGC offset table, program
  map, cell playback info `C_PBKIT`, cell position table) + `resolve_cell_spans` (cells →
  `(vob_file,start,end)` spans, splits at the 1 GB VOB-file boundary, raises on a truncated dump) +
  **`DVDCellStreamHandle`** (`open()` streams cells in PGC order with nav-strip + cell-granular
  `NavInfo` + `seek(t)→cell`). VALIDATED on the **real KUNGPOW IFOs** (independently re-parsed by a
  verify agent): VMGI 11 title sets / 16 titles; VTS_10 main feature = 1 PGC / 29 programs / 33 cells
  tiling contiguously to **3.98 GB** / 81:25 @ 29.97. **79 tests** (68 + 11; new ones synthetic +
  skip-guarded real-IFO cell-order check), all green; fixture-absent → skipped=7 (clean-clone safe);
  no copyrighted bytes committed. Flagged limits: cell-level seek (finer VOBU/DSI time-map = TODO),
  first-PGC-only (multi-PGC/multi-angle = TODO). GATE: the 480i+ADDR_ERR candidate `.rbf` is still
  build-blocked behind 573's long build (57+ min on the shared Dell); watcher patiently waiting — no
  lock-fighting. next: build the candidate when the box frees; morning HW localize via `uart_debug`.
- 2026-06-04 (cycle 10 — OVERNIGHT, candidate .rbf BUILT + morning-ready) — the 480i+ADDR_ERR
  candidate compiled cleanly on the Dell (0 errors, ~22.5 min) once 573's long build freed the box;
  converted to a compressed loadable `.rbf`: **`dell:~/NetVOB_MiSTer/core/MiSTer_MPEG2/output_files/
  mpeg2fpga_dvd_480i_addrerr.rbf`** (2.99 MB). Provenance confirmed: `emu.sv`+`modeline.v` (480i) +
  `mem_shim.sv` (ADDR_ERR fix) in the build tree. **GATE OVERNIGHT-PREP COMPLETE.** ►► MORNING TURNKEY
  STEPS: (1) `dell_coord.sh devlock mister acquire dvd`; (2) copy that `.rbf` → `mister:/media/fat/
  mpeg2fpga_dvd.rbf`; (3) `load_core` the `.mgl` (mounts a clip to S0) — clip at `/media/fat/test.mpg`
  (currently a 720×480 NTSC bars ES); (4) follow **`docs/hw-decode-diagnostic.md`**: read `uart_debug`
  (115200 8N1 on UART_TXD; try `/dev/ttyS1` over ssh, else USB-TTL, else the LED fallback) → the
  RP/P/FC/streamer counts localize the black to feed / decoder / mem_shim / video-out → pick the next
  patch (ADDR_ERR already in this build; FIFO-swap + feed-latch are staged backups in
  `core/patches/hw/`). Expectation: best case stable picture; else a stable raster that localizes the
  stage. mister untouched overnight (off-board constraint honored).
- 2026-06-04 (cycle 11 — OVERNIGHT spine, M4 disc-ID) — added `tools/discid/` (stdlib, isolated):
  `disc_fingerprint()` = stable SHA-256 (`netvob-discid-v1`) over the IFOs (sorted/order-independent,
  ignores VOB payload so sliced dumps still fingerprint stably) + `resolve_title()` chain
  **sidecar > cache > folder (+TMDB-by-name, gated on `TMDB_API_KEY`, never crashes without it)**; CLI
  `python3 -m tools.discid <VIDEO_TS>`. Validated on the real KUNGPOW IFOs (independently verified):
  fingerprint `7142b469…` (deterministic + byte-sensitive); no-key resolver → folder fallback →
  cached. 19 tests (synthetic + skip-guarded real), green; `cache.json` gitignored; no copyrighted
  bytes; no regressions outside `tools/discid/`. blocker: TMDB live path unverified (no key yet — user
  will provide; stub-tested only). **OVERNIGHT WIND-DOWN:** gate fully prepped (candidate `.rbf` +
  diagnostic plan), spine advanced (M3 PGC/cell nav + M4 disc-ID); settling to a quiet hourly
  heartbeat — `mister` HW test is the next inflection (needs the user + CRT). No more new overnight
  workstreams unless something completes/breaks.
- 2026-06-04 (decode-on-HW LOCALIZED via uart_debug — it's the **FEED**, not mem_shim) — loaded the
  candidate 480i+ADDR_ERR `.rbf` on `mister` and read the fork's `uart_debug`. **FINDING: the black
  is a FEED failure** — `mpg_streamer` is INACTIVE with `total_sectors=0` (`T:0 Z:0000 H:0 D:0`), so
  the decoder gets **no bitstream** → 0 mem reads (`P:0000`) → no video (`V:0`); meanwhile the
  raster/vsync runs (`FC` advancing) and the mem/decoder subsystem DOES init (writes to `@0x06000006`,
  `sdram_busy=1` on a clean reload). **So decoder + mem_shim + analog are NOT the blocker — the clip
  just isn't delivered.** ROOT: the `.mgl <file type="s" index="0" path="test.mpg">` mount does **not
  pulse `img_mounted[0]`** for the `S0` "Load Video" slot (tried delay 1 & 6); `emu.sv`'s
  mount→streamer wiring is correct (`start_streaming = img_mounted[0]` rising, `file_size<=img_size`).
  ⇒ the overnight **mem_shim/ADDR_ERR/FIFO work was the WRONG LAYER** (still-valid latent fixes, not
  this bug). NEXT: fix the **S0 sd_* mount mechanism** — research Main_MiSTer's mgl/mount handling
  (correct `.mgl` form, or the binary's `Mount %s as %s on %d slot` cmd syntax) so `img_mounted[0]`
  fires → then re-test decode. COORDINATION: now board-coordinated with 573 per the updated protocol
  (their note: mister free till ~19:30Z); acquired/released the devlock, posted findings. EARLIER
  MISTAKE (corrected): `dell_coord`'s 30-min age-heuristic let me STEAL 573's old-but-ACTIVE devlock,
  and I over-escalated to the human instead of reading the board — flagged the age-steal for hardening.
- BACKLOG (user idea 2026-06-04, design-only/future) — a **3rd source library: live web streams**
  (e.g. Toonami Aftermath) transcoded on the Pi to 480i MPEG-2 PS. Architecturally ≈ PlexSource (URL →
  ffmpeg → 480i NTSC PS); a clean new `Source` plugin, kept separate/badged. Build after the spine
  works. Captured in session memory.
- 2026-06-04 (FEED gate de-risked off-board + PS→ES resolved + UX north-star folded in) — three
  off-board advances while 573 held the dell build lock + mister device (no on-board work this cycle):
  **(1) FEED root-cause lead + fix staged.** Per the hub's new image-mount LESSONS (added by 573) +
  573's note, `img_size=0` almost always = FILE-NOT-FOUND — and `hw_decode_test.sh` copied the `.rbf`
  but **never staged a `test.mpg`**, so the `.mgl` mounted a path that didn't resolve → `img_size=0` →
  `total_sectors=0` → black. Built `tools/testclips/make_test480i.sh` (reproducible NTSC 480i MP@ML
  *elementary* clip, 720x480, TFF, full I/P/B GOP, ~2MB) and **verified it decodes in our Verilator
  harness at native 720x480 with real picture content** (testsrc2 timecode/sweep/checkerboard). Hardened
  `hw_decode_test.sh`: step 2b now stages the clip → `mister:/media/fat/test.mpg`, checks size+seq-header
  BEFORE load, and the interpretation block reads the gate from `uart_debug` ALONE (Z=total_sectors as a
  file-found oracle) and splits file-not-found vs mount-pulse via a manual-OSD-mount fallback. If this is
  file-not-found, the existing candidate `.rbf` needs **no rebuild**. **(2) PS→ES question resolved.**
  The `mpeg2fpga` VLD (`vld.v STATE_NEXT_START_CODE`) consumes a *video elementary stream* (handles only
  0x000001 video codes, not pack/PES); `mpg_streamer` feeds mounted sectors byte-for-byte with NO demux.
  `docs/transport.md`+`service-design.md` already decide the **ARM demuxes PS→video-ES into the sd_* seam**
  — so the decoder gets ES, and an ES test clip is architecturally exact (not a shortcut). Real DVD VOBs
  are PS → the PS→ES demux is the ARM's job (M2), flagged. **(3) UX north-star.** User steer: the core
  just plays video; controller = remote; UI = an *alternate-history DVD player* that natively browses
  Plex/network libraries. Flipped `catalog-browse.md` §3 to **OSD-first browse** (go/no-go on whether the
  stock OSD can navigate libraries), demoted the custom UI to a later **retro Plex-style** phase, added §8
  (transport/overlay/DVD-menus, honestly scoping subpicture as a NEW path); `milestones.md` M4/M6 updated
  + new **M8** experience tier; `PLAN.md` row updated; session memory written. NEXT (needs device free):
  run `tools/build/hw_decode_test.sh` when the `mister` devlock frees (573 cycling till past ~19:30Z) →
  the staged clip confirms feed→decode via uart_debug (no CRT needed for the decode gate; CRT only for the
  final analog field/color check). All on `feat-decoder-bringup`; `main`+`mister` untouched this cycle.
- 2026-06-04 (HW retest → file-not-found RULED OUT → root cause = malformed CONF_STR S-slot; fix
  staged) — re-ran `hw_decode_test.sh` with the golden clip now staged. test.mpg CONFIRMED on board
  (1995850 bytes, hdr 000001b3) yet `uart_debug` still `Z:0000 T:0 J:0000 P:0000` → **NOT
  file-not-found; it's a MOUNT-PULSE failure** (`img_mounted[0]`/`img_size` never register for the S0
  `.mgl` mount). Decoder/raster/sdram init fine (`L:1 A:1 FC` advancing `U:1`, writes @06000003). No
  standalone `mount` FIFO verb exists (binary only takes `load_core`/MGL), so the manual-mount split
  can't be done hands-off. **ROOT CAUSE (authoritative):** the CONF_STR slot was `"S0,MPG M2V,Load
  Video;"` — but MiSTer docs define `{Ext}` as a **concatenated list of 3-char extensions, NO
  separators** (`BINGEN`=BIN+GEN; `S0,CUECHD,...`). The **space** makes the 7-char field misparse into
  {MPG, " M2", V} → corrupt slot-0 filter → the `.mgl`/OSD mount to S0 doesn't cleanly register. Present
  in BOTH current and the (unverified) "prior-working-config". **FIX:** `"S0,MPGM2V,Load Video;"` —
  applied to the Mac submodule tree + the dell build tree (exactly candidate 480i+ADDR_ERR **+** this one
  line) + captured as `core/patches/hw/mpeg2fpga-confstr-sslot-ext-fix.patch`. NEXT: when the dell build
  lock frees (573 building till ~20:15Z), launch `dell_build.sh` with **NO ref** (build the dirty working
  tree) → ~3MB compressed `.rbf` → re-run `hw_decode_test.sh` → expect `Z>0` then `FC`-decode. Device +
  build both shared with 573 — coordinate via hub lock+board.
- 2026-06-04 (CONF_STR-fix build LAUNCHED on dell) — the wait-for-free watcher grabbed the box the
  moment 573 freed it (attempt 5) and launched the dvd build DETACHED at 20:06:52Z (container
  quartus-dvd; hub now permits up to 2 concurrent builds @ --cpus=2). Confirmed it is compiling the
  working tree WITH the fix (`grep` on dell shows `S0,MPGM2V`) = candidate 480i+ADDR_ERR + the one-line
  CONF_STR change. Fit risk minimal (string-constant edit on an already-fitting design). A
  completion watcher (task bhtndyy3q) polls dell:/tmp/dellbuild-dvd.log and wakes me on DONE → then:
  ensure a compressed `.rbf` (`quartus_cpf -o bitstream_compression=on` if POST_FLOW left only a .sof),
  acquire the FREE mister devlock, re-run `hw_decode_test.sh` against the new rbf, read uart_debug —
  expect Z>0 (mount fires); FC-advancing-with-reads = decode-on-HW SOLVED.
- 2026-06-04 (OBSERVATION → M7 finding: framework OSD is half-height + shifted-up on the CRT) — user
  reports the stock MiSTer OSD menu renders ~half as tall as usual and shifted toward the top. Verified
  the core's OWN 480i timing is textbook-correct (modeline.v MODELINE_NTSC_INTERL: VERT_RES=239 →
  240 active lines/field, VERT_LEN=261 → 262/field + HALFLINE=428 odd-field half-line → 525/frame,
  VID_MODE=3'b001 interlaced). So this is NOT the core modeline. Diagnosis: the framework OSD overlay
  (sys_top/osd.sv, composited over the raw VGA_* with VGA_SCALER=0) assumes PROGRESSIVE video and
  mis-positions over our interlaced (VGA_F1-toggling) raster — a known wrinkle for raw-analog-interlace
  cores. Upshot: (a) the OSD appearing interlace-distorted actually CONFIRMS the 480i interlaced output
  is live; (b) the decoded VIDEO uses the correct core modeline directly, so its geometry should be full
  480i regardless of the OSD overlay. ACTION: tracked as M7 (480i polish) — judge real video geometry
  directly once decode lights up (after the CONF_STR mount fix), then decide if the OSD-over-interlace
  overlay needs a sys_top tweak. Not blocking the decode gate.
- 2026-06-04 (CONF_STR fix did NOT clear the mount; going autonomous with RTL instrumentation) — built
  + loaded the CONF_STR-fixed .rbf (mpeg2fpga_dvd_confstr.rbf, 3.0MB); uart STILL Z:0000 T:0 -> the
  malformed S-slot extension was a real bug but NOT the mount-pulse cause (the .mgl mount-by-index
  ignores the ext filter). HPS side gives no mount log (checked /var/log, dmesg, /tmp) and there is no
  standalone mount FIFO verb, and the user (via /loop "never ask the human") wants this settled without a
  hand-mount. So: INSTRUMENTED emu.sv (emu.sv-only, low risk — repurposed the debug-only uart X/Y
  fields): X = img_mounted[0] rising-edge COUNT, Y = captured img_size[19:8]. Decisive read next build:
  X=0 -> mount never reaches the FPGA (framework/.mgl/hps_io); X>0,Y=0 -> pulsed but img_size==0
  (framework sizing); X>0,Y!=0,Z=0 -> mount fine, bug is downstream (start_streaming/mpg_streamer/
  total_sectors). Synced emu.sv -> dell build tree (diff confirmed ONLY the instrumentation delta atop
  480i+confstr), launched instrumented build detached (pid 3057399, container quartus-dvd). Completion
  watcher re-armed. Also captured M7 finding earlier: framework OSD renders half-height/shifted-up over
  the interlaced raster (core modeline verified correct 480i) — cosmetic, post-decode.
- 2026-06-04 (instrumented build DONE rc=0; HW read queued behind 573's active device test) — the
  img_mounted/img_size-instrumented build finished (31:50, rc=0); converted .sof -> compressed
  mpeg2fpga_dvd_mountdiag.rbf (~3MB) + pointed hw_decode_test.sh at it. Device is LOCKED by 573 (their
  active colored-bars flash-debug test; they posted "no rush, take your time"). Reciprocated in chat,
  queued via a device-free watcher (will grab the devlock the moment 573 releases, run the test, read
  X=img_mounted-count / Y=img_size). Advancing the Pi-side PS->ES demux (M2, FPGA-independent) off-board
  while waiting. The instrumented HW read is the next decisive step on the decode gate.
- 2026-06-04 (M2 off-board increment while queued for the device: PS->ES demux reference) — built
  service/core/ps_demux.py — the MPEG-2 Program-Stream -> video-Elementary-Stream demux that
  transport.md assigns to the ARM ingest (the decoder's VLD eats ES, not PS; production is C on the ARM,
  this Python is the reference + a fixture tool to turn a real DVD PS into a sim/HW ES clip). Walks
  pack(0xBA)/system(0xBB)/PES, concatenates the chosen video stream (0xE0) payloads, strips PES headers
  (incl. PTS), resyncs on garbage, skips audio/nav(private_stream_2)/system. 7 unit tests (hand-built
  PS packets, exact-byte asserts) — full suite 86 green. Does NOT touch the M3 PS-passthrough path.
  Still queued behind 573's device test (watcher bxbs9ohgm) for the instrumented img_mounted HW read.
- 2026-06-04 (FEED GATE SOLVED on real HW — .mgl needs an ABSOLUTE path) — the instrumented build
  read decisively: X:001 (img_mounted DID pulse — the mount mechanism was never broken) but Y:000
  (img_size==0). Per hub LESSONS that = file-not-found: the .mgl RELATIVE path="test.mpg" did not
  resolve. Rewrote the .mgl with ABSOLUTE path="/media/fat/test.mpg" (rebuild-free) and re-read:
  Y:E74 (real ~2MB size), Z:0F3B (3899 sectors = 1.99MB), T:1 (streamer ACTIVE), H:1 (cache has data),
  J:0021 (reading sectors), B:1 (decoder busy), F:1 (vbw bitstream buffer filling). **The entire
  mount -> sd_* -> mpg_streamer -> bitstream chain now works on real hardware.** Hardened
  hw_decode_test.sh to always write the .mgl with the absolute path (+ comment). So the long
  black-screen saga's feed half is DONE; none of CONF_STR/mem_shim/FIFO/latch was the cause — it was
  the .mgl path resolution. NEXT GATE (revealed): the decoder RECEIVES bitstream (F:1, G:0 no VLD
  error) but does not decode to DDR yet — W:0003 stuck, P:0000, mem_shim M:D with sdram_busy U:1 /
  ack K:0, watchdog O:1 fired. = the decoder<->DDR3 write path (the layer the overnight
  mem_shim/ADDR_ERR/FIFO work targeted). That is the next investigation.
- 2026-06-04 (NEXT GATE characterized: decoder<->DDR3 f2sdram write path is hard-stuck) — ~20s uart
  trace with the feed working: W:0003 (DDR writes), P:0000 (reads), J:0021 (streamer lba), Z:0F3B,
  M:D U:1 K:0 (shim_state D, sdram_busy=1, ack=0) all FROZEN; F:1 (vbw full), B:1 (busy), E:0 Q:0
  (decoder issuing NO mem requests), G:0 (no VLD error); only FC (raster) free-runs and the watchdog
  O cycles ~every 4s. Reading: the decoder got bitstream, made 3 DDR writes, then the f2sdram bridge
  stopped completing (sdram_busy stuck high, never acks) -> mem_shim stalls in state D -> decoder
  hangs -> streamer backpressures (J frozen, cache full) -> watchdog resets -> repeat. This is the
  HPS<->FPGA DDR3 (f2sdram) write path, NOT the feed. NEXT (sim-first, off-board): run the
  core/sim/memshim/ Verilator harness to split mem_shim LOGIC (does the write path complete vs a
  ddr3_model?) from the HW f2sdram bridge (enable/clocking/reset/base-addr at clk_mem 108MHz). If sim
  passes, the bug is HW f2sdram config; if sim stalls the same way, it's mem_shim logic.
- 2026-06-04 (decoder↔DDR3 gate localized to the DDRAM write path; mem_shim logic exonerated) — deep
  analysis of the post-feed stall: (1) the memshim Verilator harness already PROVED mem_shim decodes
  greyramp correctly through the REAL mem_shim against a conformant ddr3_model (byte-identical w/
  realistic waitrequest/latency) — so mem_shim LOGIC is fine; (2) decoded M:D = packed {cmd=WRITE,
  saved=0, state=1} (NOT raw state 13) — mem_shim is correctly WAITING in state 1 for ddr3_waitrequest
  to drop on a write; (3) the core uses the STANDARD MiSTer DDRAM_* port (DDRAM_CLK=clk_mem=108MHz,
  ddr3_burstcnt=1 always — no burst violation); (4) DDRAM_BUSY (=ddr3_waitrequest, uart U) sticks HIGH
  after exactly W:0003 writes = the framework DDR controller accepted ~3 (a small FIFO) then wedged =
  writes NOT draining to physical HPS DDR3. (5) mem_shim hardcodes the address window:
  ram_address = {7'b0011000, addr} -> bits[28:25]=0011 = "window 3 = 0x30000000" (uart @:06000003 =
  byte 0x30000018). sysmem.sv is a CUSTOM sysmem_lite (ram1/ram2/vbuf clients, f2h_sdram0/1 + address
  slave->master bridges); emu DDRAM_* -> sys_top ram_address -> sysmem ram1 -> f2h. TWO LIVE
  HYPOTHESES: (A) the hardcoded window-3 0x30000000 base is wrong/unbacked for this f2h mapping (writes
  go nowhere -> FIFO fills -> BUSY sticks); (B) the build FLAGGED unmet timing ("Design doesn't meet
  its timing requirements", "not fully constrained") -> at 108MHz DDRAM_CLK the handshake may glitch.
  NEXT: (A) read sys_dual_sdram.tcl full address map + how sysmem_lite maps ram1 address to physical
  (does window-3/0x30000000 actually exist here?), compare to upstream MiSTer sysmem_lite / a known-good
  DDR core's DDRAM base; (B) quartus_sta (hub tools/timing_triage.tcl) on the post-fit netlist for the
  DDRAM/mem_shim paths. Fix the address or clock, rebuild, retest. FEED remains solved; this is the
  last gate before decoded pixels.
- 2026-06-04 (DDRAM gate ROOT-CAUSED: timing closure, NOT the address — proven vs the working 573 core)
  — resolved the two hypotheses. (A) ADDRESS: the f2sdram bridge (f2sdram_safe_terminator) is a
  PASSTHROUGH (no framework base translation); DDRAM_ADDR is a 64-bit-WORD address (29b must be word to
  exceed 512MB), so window-3 word 0x6000000 = byte 0x30000000 = the standard MiSTer FPGA-reserved DDR
  region. Address is CORRECT — hypothesis A refuted. (B) TIMING: the build's STA is catastrophically
  failing — h2f_user0_clk Slack -101ns, emu|sys_pll general[1] -91ns (TNS -15337), pll_audio -106ns
  (TNS -13788). CROSS-CHECK vs the known-good 573 core (works on HW): its STA is CLEAN (worst -2.5ns
  hdmi; h2f +1.1ns MET; PLL +0.002 MET). The difference: 573 uses ONE edge-aligned PLL (all clocks
  legitimately related -> close); MY core has MULTIPLE independent PLLs (sys_pll/pll_audio/pll_hdmi)
  and the framework sys_top.sdc has NO set_clock_groups, so the unrelated domains are analyzed as
  related -> thousands of false cross-domain violations, AND any REAL clk_mem(108MHz)<->h2f CDC at the
  f2sdram boundary is buried + never timing-closed. ⇒ the unclosed clk_mem/DDRAM<->HPS crossing is why
  the DDRAM write handshake wedges on HW (sdram_busy stuck). NEXT (timing closure, off-board + 1
  rebuild): (1) add set_clock_groups -asynchronous cutting sys_pll vs pll_audio vs pll_hdmi vs h2f vs
  the 50MHz inputs (a core .sdc), re-run quartus_sta -> the false violations should vanish; (2) inspect
  the REAL remaining clk_mem/DDRAM/f2sdram paths — if they fail, fix the CDC or LOWER clk_mem (108->
  ~100MHz or properly synchronize the f2sdram handshake); confirm the mem_shim<->DDRAM crossing is
  registered/synchronized; (3) rebuild, re-run hw_decode_test.sh -> success = W climbing + P>0 (decoded
  frames in DDR). pll_audio is unused (no audio in mpeg2fpga) yet shows the worst slack -> likely fully
  cuttable. FEED still solved; this timing closure is the last gate before decoded pixels on HW.
- 2026-06-04 (COURSE-CORRECTION: the timing "failure" is a benign constraint ARTIFACT, not the cause;
  new lead = f2sdram_safe_terminator) — drilled into the worst clk_mem path with quartus_sta
  (tools/build/sta_clk_check.tcl): clk_mem(108)→clk_mem intra slack -91.8ns BUT
  arrival=104.5ns, required=12.7ns, **num_logic_levels=1**. One logic level cannot really take 104ns
  (real 1-level delay ~1-3ns) ⇒ the -91.8ns is a CLOCK-RELATIONSHIP/CONSTRAINT artifact, NOT real
  routing — the f2sdram path's logic is trivial and fine at 108MHz. So LOWERING clk_mem would NOT help;
  timing is a RED HERRING. Both prior hypotheses now refuted (address = correct 0x30000000 MiSTer FPGA
  region; timing = benign artifact). NEW LEAD: the failing node is
  sysmem|f2sdram_safe_terminator_ram1|write_terminating -> HPS f2sdram. The safe_terminator is the
  MiSTer module that ABSORBS/terminates f2sdram transactions during reset (so the HPS bridge doesn't
  lock). If it is stuck in terminating mode (its reset/enable never deasserts, or it never sees the HPS
  f2sdram bridge ready), writes get absorbed/blocked and DDRAM_BUSY sticks after a few -> EXACTLY the
  W:0003-then-wedge symptom. NEXT: read sys/f2sdram_safe_terminator.sv (when does write_terminating
  assert? what's its reset/exit condition? is ram1_reset/reset_out stuck?), check whether the HPS
  f2sdram bridge is enabled/ready on this build, and compare the reset wiring to a known-good HPS-DDR
  MiSTer core. The fix is likely in the f2sdram reset/enable or bridge bring-up, NOT timing or address.
- 2026-06-04 (BREAKTHROUGH: f2sdram-broken CONFIRMED by reboot — decoder now does REAL DDR traffic) —
  user-authorized warm reboot, then loaded the dvd core FIRST on the clean boot. RESULT (uart):
  W:00BD (189 writes, was stuck at 0003), P:0054 (84 reads, was 0), RP:0053 (83 read responses, was 0),
  @:060000B9 (mem addr advanced). ⇒ the DDR wedge WAS a broken HPS f2sdram interface inherited from
  prior core-load resets (exactly as f2sdram_safe_terminator.sv documents: mid-stream-terminated
  transactions wedge f2sdram until an HPS reset; a load_core does NOT fix it). The reboot cleared it and
  the decoder DECODED real frame data (writes+reads to DDR). HUGE — the whole feed→decode→DDR path works
  on a fresh boot. BUT it RE-STALLS at W:189: W/P/@ freeze, U:1 (waitrequest) stuck again, watchdog O
  cycling ~4s, E:0 Q:0 (no new mem reqs). So something re-wedges f2sdram after ~189 single-beat
  (burstcnt=1) transactions. TWO leading sub-hypotheses for the re-stall: (A) the decoder WATCHDOG
  (watchdog_rst) resets mem_shim mid-transaction -> yanks a live f2sdram write -> re-breaks the
  just-cleared f2sdram; (B) f2sdram dropped a read response (P:84 issued vs RP:83 received = 1 gap) and
  mem_shim hangs waiting for it — EXACTLY the +ddr_drop hang the overnight core/sim/memshim run predicted.
  NEXT: (1) check what watchdog_rst resets in emu.sv (does it reset mem_shim/reset_n, bypassing the
  safe_terminator? if so, route it through reset_out OR disable the watchdog so it can't re-break
  f2sdram); (2) revisit the memshim sim's dropped-response hang + whether mem_shim needs a
  read-response timeout/recovery; (3) rebuild with the fix, reboot fresh, retest -> success = W climbs
  past 189 + a full frame decodes. Board returned to MENU + released after the test (no flicker).
  REBOOT IS USER-AUTHORIZED (shared HW); routine device hand-off stays session-to-session.
- 2026-06-04 (FIX crafted + SIM-VALIDATED: serialize mem_shim reads to stop the f2sdram re-stall) —
  root cause of the W:189 re-stall = mem_shim's 2-state FSM advances on command ACCEPTANCE not read
  RESPONSE, so it issued a WRITE on top of an outstanding READ; on real HW that wedges the HPS f2sdram
  (waitrequest stuck) and a lost/misordered response permanently desyncs the response stream (uart
  P:84 reads vs RP:83 responses) — the exact UNFIXED hang the overnight memshim sim flagged under
  +ddr_drop. FIX (mem_shim.sv): single-outstanding-read serialization — a read_pending flag set when a
  read is ACCEPTED, cleared on its readdatavalid; while set, S_IDLE holds off issuing the next command
  AND stops pulling the request FIFO (no command lost). SIM-VALIDATED in core/sim/memshim: still decodes
  greyramp correctly through the REAL mem_shim (2 framestore + 3 tv_out frames; framestore_0000 = I
  frame, mean=128 greyramp, nonzero) — serialization does NOT break decode. Also fixed run_memshim.sh
  (set -u + macOS bash3.2 empty-array crash on no-plusargs). Patch:
  core/patches/hw/mpeg2fpga-memshim-serialize-reads.patch. NEXT: sync mem_shim.sv -> dell, build
  detached, then (USER-AUTHORIZED reboot) reload-core-first + retest -> expect W to climb past 189 and a
  full frame to land in the framestore = DECODE-ON-HW COMPLETE.
- 2026-06-04 (drift reconciled: Mac mem_shim was MISSING the collision-guard that dell had) — while
  syncing the serialize fix to dell, the diff exposed that the dell build tree had an ADDR_ERR
  "COLLISION GUARD" (don't let a synthetic-0 ADDR_ERR response overwrite a real same-cycle
  readdatavalid -> would corrupt a reference -> garbage) that the Mac tree never had — it was added on
  dell only (the candidate .rbf included it). Reconciled by adding the collision guard to the Mac
  mem_shim too (now the complete source of truth = addrerr + collision-guard + serialize), re-validated
  in the memshim sim (2 framestore + 3 tv_out, I-frame mean=128 — decode still correct with BOTH fixes),
  re-synced Mac->dell (now identical), regenerated the patch (now contains both). Lesson: the dell tree
  can drift ahead of the Mac; always diff before overwriting. Launching the build with the complete
  tree (480i + confstr + emu instrumentation + mem_shim addrerr+guard+serialize).
- 2026-06-05 (serialize+guard build DONE rc=0; .rbf ready; awaiting reboot for the retest) — the
  mem_shim serialize-reads + collision-guard build finished (32:14, rc=0); converted .sof ->
  mpeg2fpga_dvd_serialize.rbf (~3MB); pointed hw_decode_test.sh at it. The f2sdram is currently WEDGED
  from the prior test, so the clean retest needs a user-authorized warm reboot. Expected on retest: W
  climbs past 189 and keeps going (no re-stall) + a full frame decodes = decode-on-HW complete.
- 2026-06-05 (serialize fix: ELIMINATED the f2sdram wedge but over-corrected — f2sdram needs PIPELINING)
  — clean-boot retest of the single-outstanding-read build: W:0000 (zero writes, was 189), P:0001 (1
  read issued), RP:0000 (NO response), but crucially U:0 + M:0 = the f2sdram is HEALTHY (not wedged;
  was U:1 stuck). So serialization PREVENTED the wedge (confirming the wedge cause = WRITE issued on top
  of an outstanding READ). BUT it deadlocked: an ISOLATED single read gets NO readdatavalid response,
  whereas pre-serialize PIPELINED reads DID respond (P:84/RP:83). ⇒ the HPS f2sdram returns read
  responses only when the master keeps the bus pipelined; forcing one-at-a-time blocks on the first
  read (and the watchdog reset + stuck read_pending make it permanent). PURE SERIALIZATION IS THE WRONG
  LEVER. REFINED ROOT CAUSE: the wedge is specifically a WRITE-on-outstanding-READ; READS pipeline fine
  and respond. NEXT FIX: allow pipelined reads (don't block them) but hold off WRITES while any read is
  outstanding (outstanding_reads counter ++ on read-accept, -- on readdatavalid; block write issue when
  >0) + a read-response TIMEOUT to recover from the rare genuinely-lost response (the 1-in-84 that
  desynced pre-serialize) so writes don't block forever. Sim-validate in memshim incl. +ddr_drop, then
  build + reboot-retest (reboot now standing-authorized). Board returned to MENU + released.
- 2026-06-05 (CORRECTED fix strategy: do NOT stall the bus — bound outstanding reads + sim-iterate
  vs +ddr_drop) — re-analysis: the planned "hold WRITES while a read is outstanding" would ALSO
  deadlock, because the serialize test proved the HPS f2sdram only returns read responses while the bus
  stays PIPELINED (an isolated read gets none). ANY stall starves it. Re-read of the pre-serialize wedge
  (state=1 cmd=WRITE, U:1 stuck, P:84 RP:83): a WRITE was blocked because an outstanding READ's response
  was LOST (1-in-84) and the f2sdram holds waitrequest until that read completes -> write waits forever
  -> wedge. Likely loss mechanism: the mem_shim RESPONSE path (mem_res_wr_en <= ddr3_readdatavalid)
  does NOT check mem_res_wr_almost_full, so if too many reads are in flight a response overflows the
  response FIFO and is dropped. CORRECT FIX (non-stalling): BOUND outstanding reads to the response-FIFO
  margin (>1 so the f2sdram still pipelines + responds, <=margin so it can never overflow -> no lost
  response -> no desync -> no write-wedge). Validate the WRONG way to fail and the fix via the
  core/sim/memshim ddr3_model fault injection (+ddr_drop / +ddr_dup reproduce the desync hang per the
  README) — iterate in SIM (seconds), not on HW (35-min builds). Replace the current read_pending
  (full-serialize) accordingly.
- 2026-06-05 (FIX v2 implemented + SIM-VALIDATED: pipelined reads + cause-agnostic read-response
  recovery) — replaced the (deadlocking) full-serialize with: keep the bus fully PIPELINED (decoder
  self-throttles; never stall — the f2sdram needs pipelining to respond), track outstanding_reads
  (++ on read-accept, -- on readdatavalid), and if a response is overdue by >2^17 clk_mem cycles (far
  beyond real latency -> genuine loss) synthesize one 0-response so the decoder re-syncs and the
  write it blocks can proceed. Preserved the ADDR_ERR collision guard. SIM (core/sim/memshim): no-drop
  still decodes greyramp correctly (2 framestore + 3 tv_out, I-frame mean=128); +ddr_drop=8 (drop every
  8th read response) RECOVERS -> 2 framestore frames, no STALL/hang (the failure mode that wedged HW).
  Response FIFO is 128-deep/almost_full=64 with the decoder throttling at 16, so no overflow -> the
  loss is a timing/quirk, handled cause-agnostically. CAVEAT: if HW wedges via f2sdram waitrequest-stuck
  (unrecoverable by RTL, needs reboot) this won't un-wedge the bridge, but it prevents the decoder hang
  and is the right next HW experiment. Patch: mpeg2fpga-memshim-pipelined-readrecovery.patch (supersedes
  the serialize patch). NEXT: sync -> dell (diff-verify), build, reboot-retest.
- 2026-06-05 (v2 build DONE rc=0; .rbf ready; rebooting to test pipelined+recovery on HW) — converted
  .sof -> mpeg2fpga_dvd_readrecovery.rbf (~3MB); pointed hw_decode_test.sh at it. Standing-authorized
  reboot test next (clear the f2sdram + load v2 first). Expected: W climbs past the old 189 stall and
  keeps going = decode-on-HW.
- 2026-06-05 (MILESTONE: decoder genuinely DECODING on HW — v2 ran 49x further; remaining = rare
  f2sdram read-response loss) — v2 (pipelined + read-response timeout-recovery) clean-boot test:
  W:23E8 (9192 DDR writes, was 189), P:31BF (12735 reads, was 84), RP:31B9 (12729 responses),
  J:008B (139 sectors fed). The decoder did ~22k real DDR ops (reading/writing reference frames) =
  the decode PIPELINE WORKS ON HARDWARE. Still freezes at W:9192 (U:1 waitrequest stuck, M:D
  state-1 holding a WRITE). KEY: P:12735 vs RP:12729 = 6 read responses LOST out of 12735 (~0.05% —
  a RARE random loss, hallmark of MARGINAL TIMING on the 108MHz f2sdram interface, not a systematic
  bug). When a lost response coincides with a pending write, the HPS f2sdram waitrequest-LOCKS (the
  illegal state the safe_terminator docs say only an HPS reset clears); the RTL recovery keeps the
  decoder alive across losses but cannot un-lock the bridge. So v2 is a big step but not full decode.
  The readdatavalid path is synchronous to clk_mem (no CDC) -> a synchronous 0.05% drop = a real
  setup/hold marginal path at 108MHz. NEXT LEAD: LOWER clk_mem / DDRAM_CLK (108 -> ~100MHz or less,
  keeping a clean ratio to clk_sys 27MHz and the mem FIFOs) to make the f2sdram interface meet timing
  reliably -> eliminate the drops -> no lock -> sustained decode. Alternatives if that doesn't suffice:
  add a registered/retimed stage on the f2sdram readdatavalid/inputs; or bound outstanding reads.
  Board returned to MENU + released; v2 is a committed rollback point.
- 2026-06-05 (STA RULES OUT timing — do NOT lower clk_mem; the bridge lock is a LOGICAL/handshake
  corner) — per 573's "prove it's timing before fixing timing" discipline, ran quartus_sta on the v2
  post-fit netlist (no rebuild). RESULT: the worst REAL clk_mem->clk_mem path (levels=3) CLOSES at
  +1.496ns (mem_request_fifo dout -> mem_shim ram_writedata); clk_sys closes +22ns. EVERY negative-slack
  path is a levels=1 ARTIFACT (-88..-92ns) at the HPS f2sdram boundary (f2sdram_safe_terminator /
  ram_write / outstanding_reads -> f2sdram~FF_*) — the bogus clock-relationship math, NOT real delay.
  ⇒ the DDR interface MEETS timing; lowering clk_mem to 81MHz is CANCELLED (would cost throughput and
  fix nothing). The ~0.05% lost responses / bridge waitrequest-lock are a LOGICAL/handshake corner, not
  marginal timing. OPEN QUESTION: is P-RP=6 a steady drip of drops over the 12735 reads, or just the 6
  reads in-flight at the instant the bridge locked? Can't tell from one snapshot. NEXT (principled, not
  guessing): INSTRUMENT the lock onset — latch {state, ram_address, saved_cmd, outstanding_reads, the
  P-RP gap, FC} at the FIRST cycle ddr3_waitrequest sticks high (and whether P-RP grows during normal
  decode) -> pins whether it's drops-over-time vs a lock event + the exact command/address that wedges.
  Rebuild with that, reboot-retest, read the latched lock-condition. (573 saved a wasted build here.)
- 2026-06-05 (lock-onset PROBE added — observe-only; building) — added a bridge-lock probe to mem_shim
  (repurposed the unused debug_read_pend_cycles -> uart PC field): counts consecutive
  waitrequest-high-while-command-asserted cycles; at >=256 (sustained refusal=LOCK) latches a sticky
  {wedged, lock_cmd={read,write}, lock_outstanding} + a saturating recovery_count (how many synthetic
  responses the timeout injected = genuine dropped responses). PC decode: bit15=wedged, [14:13]=cmd
  (10=read 01=write), [12:7]=outstanding-at-lock, [6:0]=recovery_count. recovery_count>0 = the lock is
  drop-related; ==0 = a command-specific lock. memshim sim still decodes greyramp correctly (observe-
  only, decode path untouched). NEXT: build, reboot-retest, read PC -> pins the lock cause -> targeted
  fix. Auto-logging the HW result to the shared testlog this cycle too (human wants dashboard visibility).

- 2026-06-05 (lock-probe RESULT pins the cause = OUTSTANDING-READ THROTTLE; bound-reads fix built) —
  reboot-test of the lock-probe build: uart PC:C306 = wedged=1, lock_cmd=10 (READ), lock_outstanding=6,
  recovery_count=6, alongside W:29931/P:14018 (3x past v2 — the recovery kept decode alive far longer).
  DECODE: the bridge LOCKS on a READ issued with 6 reads already in flight, AND recovery_count=6 means
  6 responses were genuinely dropped (and successfully recovered) before the lock. ⇒ TWO facts: (a) real
  drops happen (rare), and (b) the HPS f2sdram has an OUTSTANDING-READ THROTTLE — issuing a new read with
  too many in flight (6 here) is what wedges it, not the drops themselves. TARGETED FIX (built): bound
  outstanding reads to READ_LIMIT=4 (below the 6 that locked) via a non-pulling read_throttled gate in
  S_IDLE (`next_is_read && outstanding_reads>=4` -> hold the next READ, don't pull the FIFO, lose nothing;
  in-order writes behind it wait briefly; resp_timeout still drains any genuinely-lost read so a throttled
  read can never deadlock). Keep the timeout-recovery for the rare real drop. SIM-VALIDATED in the memshim
  Verilator bench: no-drop decode -> 2 framestore frames (FRAME_0 mean=128, clean); +ddr_drop=8 (every 8th
  read response dropped) WITH the throttle -> 15 tv_out frames, no stall = throttle+recovery recover under
  drops without deadlock. NEXT: sync mem_shim->dell (diff-verify), detached build mpeg2fpga_dvd_boundread.rbf,
  reboot-test; SUCCESS = W climbs into the tens-of-thousands and KEEPS advancing / completes frames with no
  permanent waitrequest-lock = sustained decode. If it still locks, read PC: a lower lock_outstanding => drop
  READ_LIMIT further (e.g. 2); a WRITE lock_cmd => the wedge is write-side, add lock_addr to the probe.

- 2026-06-05 (MILESTONE: f2sdram LOCK FIXED — bound-reads build runs the FULL clip on HW, no
  wedge, REPRODUCED; new gate = display path) — built + reboot-tested the READ_LIMIT=4 bound-reads
  mem_shim (caps outstanding reads at 4, below the 6 that locked; non-pulling read_throttled gate in
  S_IDLE; FSM hand-verified: max-4-in-flight, resp_timeout backstop = no deadlock). HW result across
  TWO clean runs (reboot, then re-feed): W:EF8F=61327 writes (2x the lock-probe's 29931, 6.7x v2's
  9192), P==RP matched every sample (no runaway response loss), U:0 (ddr3_waitrequest FREE the whole
  time), M:0 (shim idle/clean), PC:0000 (lock-probe NEVER tripped = wedged=0, bridge never locked).
  J==Z==0F3B constant = all 3899 sectors of the clip fed + consumed; W freezes at end-of-clip (no
  more to decode) NOT from a stall (U:0/M:0 prove the bridge is idle). FC output-frame counter climbs
  steadily+continuously = output raster alive at ~field rate. ⇒ the HPS f2sdram outstanding-read
  throttle that wedged every prior build is SOLVED by bounding reads to 4. The hardest, longest-
  standing gate (the bridge lock) is closed; decode is sustained on hardware. Board -> MENU, devlock
  released. NEW OPEN GATE (display path): the HDMI/scaler screenshot is BLACK even during ACTIVE
  decode (captured a 6-frame filmstrip while W & FC were climbing — all mean=0, fully black). So
  decoded pixels are not reaching the HDMI output. Two unresolved-remotely possibilities: (a) the core
  is VGA/analog-only by design (picture is on the CRT via ADV7125, invisible over SSH/HDMI), or (b) a
  decode->display routing gap (the mrchrisster port's original 'no confirmed video output' state).
  NEXT: chase the display path — inspect the scaler/VGA_* + FB_* routing in emu.sv, and read the
  decoded framestore straight out of the 0x30000000 DDR window to render a frame PNG camera-free (the
  definitive, display-independent proof the decoded PIXELS are correct, mirroring the sim framestore).

- 2026-06-05 (display-gate REFRAME via workflow + camera-free readback tooling staged) — ran a
  multi-agent workflow (display-path-verify-plan) to research why the HDMI screenshot is black.
  KEY FINDING that corrects my earlier guess: the MiSTer scaler (ascal) ALWAYS runs (.run(1) in
  sys_top) and writes the screenshot framebuffer REGARDLESS of VGA_SCALER — so a black screenshot
  does NOT mean "analog-only by design"; it means the decoder's DISPLAY RASTER (core_r/g/b / VGA_DE)
  is itself black. ⇒ the gate is a decode→DISPLAY-READOUT gap (the mrchrisster port's original "no
  confirmed video output"), NOT the analog routing. Implication: a VGA_SCALER=1 diag build likely
  would NOT un-black it. DECISIVE TEST instead = read the decoded framestore straight out of DDR
  (bypasses the whole display path): proves whether the decoder wrote a correct IMAGE. Verified
  layout from the bench (mem_ctl.v write_mb/write_row): Y plane is ROW-MAJOR CONTIGUOUS from
  FRAME_n_Y, 90 words/row (720px) x 480 rows; phys = 0x30000000 + word*8; pixel_0 = word MSB (so
  little-endian readback reverses each 8-byte word); samples signed -> +128. Staged the tooling:
  tools/build/dump_framestore.py (runs on the MiSTer — mmap /dev/mem O_SYNC for f2sdram coherency,
  streams the DDR window to stdout) + tools/build/render_framestore.py (Mac — renders all 4 FRAME_n
  Y planes to PNG, flags the non-flat one). The mister has python3, so no cross-compile. Device is
  currently locked by 573 (hyperbbc573) — QUEUED; will run the readback when it frees. If the
  framestore shows the test pattern => decode-to-pixels CONFIRMED, gate isolated to the
  framestore→core_r/g/b readout RTL (resample/yuv2rgb/video-out). If garbage => decode itself.

- 2026-06-05 (FRAMESTORE DDR-READBACK works; decode is PARTIAL/DEGRADED on HW — new gate is
  decode-correctness, not display) — ran the camera-free readback: loaded the bound-reads core,
  let it decode, mmap'd /dev/mem O_SYNC at 0x30000000 on the mister (it has python3), streamed the
  14MB window to the Mac, rendered the FRAME_n Y planes (tools/build/dump_framestore.py +
  render_framestore.py). Verified addressing against mem_codes.v (MP@HL: WIDTH_Y=18/WIDTH_C=16;
  FRAME_n_Y words 0x0/0x60000/0xC0000/0x120000; phys=0x30000000+addr*8) and the layout against the
  bench write_mb (row-major, 90 words/row x 480; pixel_0=word MSB so little-endian readback reverses
  each 8-byte word; Y unsigned centered 128 -> NO +128 bias). RESULT: the framestore holds REAL
  structured data (not black) — the top-left TIMECODE box decodes cleanly — BUT the static test-
  pattern BARS are MISSING: HW frames are mean=128 stddev~7-12 (mostly flat gray). GROUND TRUTH via
  ffmpeg on the same clip: every one of its 90 frames is mean=128 stddev~65 (full bars). The clip
  LOOPS on HW (FC reached thousands of fields > 90 frames) and never has a gray frame, so the gray
  capture is NOT 'ends on gray' — it's DEGRADED DECODE. Signature: INTRA content (timecode) decodes,
  INTER-predicted background washes to flat gray. G:0 (no VLD errors), P==RP/PC:0000 (no dropped
  responses this run) -> bitstream parse is fine and no mem drops -> the degradation is in the
  RECONSTRUCTION/REFERENCE path, HW-specific (sim decodes this clip's first frames WITH bars). Side-
  by-side saved /tmp/dvd_shots/compare_ref_vs_hw.png (sent to user). ⇒ The 'video out' gate is a
  DECODE-CORRECTNESS issue (mostly-DC/gray reconstruction), not the analog/scaler display path.
  Device released, board to MENU. NEXT (root-cause): (1) make an all-I-frame (intra-only) 480i test
  clip with ffmpeg so the I-frame can be caught on HW (clip loops too fast to snapshot the I-frame in
  the normal clip) -> does pure intra decode reproduce the bars on HW? If yes, the bug is in
  inter-prediction/motion-comp reference fetch; if no, intra reconstruction (IDCT/coeff) is wrong on
  HW. (2) examine whether f2sdram READ data is correct (not just present): the reference reads may
  return wrong-but-not-dropped data under the throttle. (3) compare sim-vs-HW same frame.

- 2026-06-05 (ALL-INTRA HW test: intra decode is ALSO degraded -> the gate is HW decode-CORRECTNESS,
  likely a pre-existing port bug in the memory datapath) — fed the all-I clip (tools/testclips/
  test480i_ntsc_allI.m2v, 90 I-frames, ffmpeg stddev~65 every frame) and read back the framestore.
  RESULT: still degraded — FRAME_1 stddev=21 (FRAME_0=10.5; FRAME_2/3 flat = unused, as expected for
  all-intra), mostly gray+scattered noise with only the intra TIMECODE box clean, NO bars. So the
  degradation is NOT confined to inter-prediction: INTRA RECONSTRUCTION loses content on HW (the large
  flat-DC bar regions wash to gray). U:0/M:0/PC:0000 (no lock, no mem drops, recovery never fired), G:0
  (no VLD errors). Since the SAME RTL decodes this clip correctly in sim, the bug is HW-SPECIFIC, and
  most likely a PRE-EXISTING port decode-correctness bug in the memory datapath (the biggest sim-vs-HW
  difference) that was simply never observable before because the decoder always LOCKED first — i.e.
  the bound-reads fix removed the lock and exposed the next layer (consistent with the port's documented
  'no confirmed video output'). VBUF (bitstream ring buffer @ word 0x1c0000 / phys 0x30E00000) readback
  anomaly: only 101 start codes (97 picture-starts) vs the source clip's 3150 (mostly slices) — HINTS
  the f2sdram may return correct-COUNT-but-wrong read DATA, corrupting the bitstream the VLD reads back
  (which would explain valid-but-wrong decode with G:0). INCONCLUSIVE (ring-buffer state at snapshot).
  Dumps saved: /tmp/dvd_shots/fs_allI.bin, fs_allI_big.bin (16MB incl VBUF). Board MENU, released.
  NEXT: root-cause workflow — (a) is f2sdram READ DATA correct (not just count)? design a memory
  round-trip integrity test through the FPGA; (b) trace VBUF-read→VLD→IQ/IDCT→framestore-write for what
  yields DC/low-freq-lost gray on HW only; (c) is the degradation correlated with the mem_shim recovery/
  throttle or pre-existing? Then a targeted fix (likely a build).

- 2026-06-05 (VBUF bitstream ROUND-TRIPS INTACT -> bug is DOWNSTREAM, Altera-port datapath suspect;
  supersedes the earlier 'VBUF anomaly' guess) — byte-compared the VBUF readback (from fs_allI_big.bin,
  word 0x1c0000 / phys 0x30E00000) against the SOURCE clip. Raw bytes didn't match, BUT after reversing
  each 8-byte word (the bitstream is stored 64-bit-word MSB-first, same convention as the framestore),
  it MATCHES: start codes 101 -> 2204 after per-word reversal (source has 3150; VBUF is a ~1.6MB ring
  holding ~70% of the 2.38MB clip), and 17/40 high-entropy source chunks found verbatim in the ring.
  ⇒ the compressed bitstream the decoder reads back from DDR is CORRECT (f2sdram write path + storage
  clean), and G:0 confirms the VLD parses valid data. This RULES OUT bitstream/VBUF corruption and the
  earlier 'f2sdram returns wrong read data' hint for the bitstream path. The degradation is DOWNSTREAM
  of the bitstream: in the coefficient-decode -> inverse-quant -> IDCT -> reconstruct -> framestore-write
  chain. Since intra has no reference read and the bitstream is intact, the prime suspect is now an
  ALTERA/CYCLONE-V RAM/ROM/FIFO INFERENCE difference (sim-clean in Verilator/Icarus, wrong on real
  silicon: uninitialized BRAM, read-during-write semantics, a coefficient/dequant ROM, the IDCT
  transpose RAM). NOTE: the root-cause WORKFLOW (decode-degradation-rootcause) FAILED this cycle — all 3
  schema'd explorers returned null + the synth hit a session limit (resets 9pm PT). RETRY next cycle,
  lighter (inline RTL reads, no strict StructuredOutput schema). NEXT: trace the IDCT/iquant/coeff-RAM
  modules in rtl/mpeg2/ for Altera inference hazards (compare to any known mpeg2fpga Altera-port notes);
  candidate cheap HW confirm = a solid-color / single-DC-block clip (does even a flat block reconstruct?).

- 2026-06-05 (ROOT-CAUSE LEAD: the rewritten dual-clock FIFO is the prime suspect — it was NEVER
  validated by the full-decode sim) — inline RTL diff port-vs-upstream (core/mpeg2fpga is vendored):
  the ONLY substantive datapath change is xilinx_fifo_dc.v (240 lines — a full rewrite to a Gray-code
  async FIFO for Cyclone V). iquant.v's 14 lines are a cosmetic `do`->`dout_wire` rename (do = Quartus
  reserved word); idct/rld/recon/framestore/vld all 0 changes; wrappers.v's 1 line (~rst->rst) correctly
  matches the new FIFO's active-low reset. CRITICAL: the decode-validating sim (core/sim/run_hwclip,
  which produced the correct bars) runs the UPSTREAM core/mpeg2fpga/bench/iverilog bench against the
  ORIGINAL rtl — i.e. the ORIGINAL Xilinx FIFO, NOT the port's rewrite. The rewritten Gray FIFO is only
  exercised by the narrow memshim sim, NEVER by full decode. It sits on the clk<->mem_clk framestore CDC
  path (framestore.v: fifo_dc wr_clk(clk)/rd_clk(mem_clk) for writes, mem_clk/clk for read-back). And
  sys_top.sdc has NO false_path/max_delay on the decoder CDC synchronizers (only framework OSD/VGA/FB).
  ⇒ PRIME SUSPECT = the rewritten xilinx_fifo_dc.v corrupts framestore data crossing clk<->mem_clk on HW
  (logic bug the original-FIFO sim couldn't catch, or unconstrained-CDC metastability). Signature fits:
  DC/low-freq coefficients lost (flat bars -> gray) while high-freq (timecode edges) partly survive.
  DECISIVE TEST (FPGA-independent): run the FULL decoder sim with the PORT's xilinx_fifo_dc.v swapped in
  for the original. Degrades in sim => FIFO LOGIC bug (fix + sim-validate, fast, no 30-min HW loop);
  stays correct => FIFO logic fine, HW issue is CDC timing (add SDC false_path/sync constraints, rebuild).
  Building that sim test next.

- 2026-06-05 (FIFO EXONERATED in sim — pivot to inferred-RAM; "prove it before building" saved a HW
  build) — built the full decoder sim with the PORT's rewritten Gray-code xilinx_fifo_dc.v swapped in
  for the bench's known-good generic_fifo_dc (hybrid wrappers: bench soft single-clock FIFO + port Gray
  dual-clock FIFO; /tmp/fifo_test/run_fifo_swap_test.sh, Verilator). Ran port-FIFO vs baseline on the
  same stream.dat (testbench drives ASYNC clocks clk=75MHz / mem_clk=125MHz / dot=27MHz). RESULT:
  framestore frames 0,1 BYTE-IDENTICAL; frame 2 differed ONLY in the dump's timestamp comment
  (17.29ms vs 18.45ms) — the entire pixel BODY (all 4 frame buffers + OSD) is byte-identical
  (body-md5 equal). So the port Gray FIFO decodes BIT-FOR-BIT identically to the reference FIFO in sim
  (just ~1ms slower latency). ⇒ the rewritten FIFO LOGIC is CORRECT and is NOT the HW degradation cause.
  (Almost rebuilt HW on a FIFO fix — the sim test prevented a wasted 30-min build; 573's prove-it
  discipline again.) REORIENT: the HW corruption is SYSTEMATIC/reproducible (consistently gray each
  run), which argues AGAINST random CDC metastability and FOR a DETERMINISTIC inferred-RAM/ROM
  difference in an UNCHANGED datapath module — Verilator simulates reg-array memories ideally, but
  Quartus INFERS hardware RAM whose read-during-write / init semantics can differ on Cyclone V. Prime
  new suspect: the IDCT transpose RAM (idct.v) and/or iquant weighting-matrix RAM (iquant.v has 2
  initial blocks) — "DC/low-freq lost -> flat gray" is a classic 2D-IDCT transpose-RAM read-during-write
  corruption. NEXT: inspect idct.v / iquant.v / coefficient-buffer RAM inference (read-during-write mode,
  ramstyle, init dependence); compare how they'd infer on Altera vs the behavioral sim; candidate fix =
  force the correct read-during-write mode / add ramstyle / explicit init. Cheap HW discriminator if
  needed: does even a solid-DC-only block reconstruct?

- 2026-06-05 (build-log smoking-gun candidate: Warning 276027 dual-clock-RAM read-during-write
  UNDEFINED; + test-gap: FIFO swap used greyramp not the failing clip) — grepped the Quartus build log
  (dell:/tmp/dellbuild-dvd.log) for RAM-inference messages. The single-clock FIFO/dpram RAMs (xfifo_sc,
  the intra/non_intra quant matrices) all got Warning 276020 "Pass-through logic ADDED to MATCH the
  read-during-write behavior" = HANDLED. But the PORT's dual-clock FIFO RAMs (framestore's
  mem_request_fifo + mem_response_fifo, xilinx_fifo_dc mem_rtl_0) got Warning 276027 "read-during-write
  behavior of a dual-clock RAM is UNDEFINED and may NOT match the original design" = NOT handled. This
  is the exact sim-vs-HW gap: the FIFO LOGIC is correct (sim proved bit-identical decode) but Verilator
  models the internal mem[] with DEFINED read-during-write while Quartus infers a dual-clock M10K whose
  same-address R/W is undefined. CAVEAT: a correct async FIFO avoids same-address R/W (the 2-stage
  gray-pointer synchronizer latency means a read only hits a location written >=2 rd_clk cycles earlier),
  so 276027 may be a conservative/benign warning. TEST GAP found: my FIFO-swap sim used the default
  greyramp stream, NOT the test480i bars/timecode clip that actually degrades on HW — greyramp may not
  exercise the failing condition. NEXT (decisive): rebuild stream.dat from tools/testclips/
  test480i_ntsc.m2v (or _allI) and re-run the port-FIFO-vs-baseline sim on THAT content. If port FIFO
  degrades in sim with the real clip => reproduced + debuggable in sim. If still bit-identical => the
  FIFO is truly fine and the HW bug is the 276027 dual-clock-RAM read-during-write (fix: force defined
  R/W via explicit altsyncram/ramstyle or restructure mem[]) OR a CDC-timing/other-inference issue.

- 2026-06-05 (FIFO FULLY EXONERATED — bars clip too; pivot to arithmetic/multiplier inference) — re-ran
  the port-FIFO-vs-baseline sim on the REAL failing clip (test480i_ntsc.m2v bars+timecode, not greyramp):
  framestore_0001 body BYTE-IDENTICAL, both stddev=56.1 (correct bars). So even on the exact content that
  degrades to gray on HW, the port Gray FIFO decodes bit-identically to the reference in sim. ⇒ FIFO
  LOGIC definitively NOT the bug (proven on 2 streams), and Warning 276027 is almost certainly benign
  (correct async FIFO avoids same-address R/W via synchronizer latency). The HW degradation is a
  DETERMINISTIC, Verilator-invisible silicon behavior. Remaining candidates: arithmetic/MULTIPLIER
  inference differences in the IDCT/iquant (signedness/width -> systematic attenuation toward gray fits
  the "DC/low-freq lost" signature better than RAM), other inferred-RAM init/RDW, or a synthesis-opt
  difference. NEXT: grep the build log for DSP/multiplier inference; inspect idct.v/iquant.v multiplies
  for signed/width hazards; consider a targeted HW probe (uart-checksum the IDCT output, or a known-DC
  block test). Committed through here.

- 2026-06-05 (SPINE PROGRESS: live PS-over-TCP → sd_* seam wired + proven end-to-end, FPGA-independent)
  — diversified off the decode-correctness bug (per "advance every unblocked track"). The arm/ ingest
  had mature host-tested components (ps_demux, ringbuf, feeder — all tests green, incl. a real DVD-slice
  fixture) but NO actual network glue (no socket code anywhere; realpipe modeled recv() with file reads).
  Built the missing piece: arm/netingest.{h,c} ties the components into the live seam — TCP recv() ->
  ps_demux -> video ES -> ring -> ni_get_sector() (one sector per future sd_rd request; the sd_* seam
  stays abstracted, on HW it becomes sd_buff_dout/sd_buff_wr). Plus arm/netd.c = the runnable TCP daemon
  (connects to the Pi provider's media socket, recv-loops with ring backpressure, drains whole sectors).
  arm/tests/test_netingest.c (added to the suite, all 6 arm tests green): chunked-recv -> full-sector
  pull == demuxed video ES byte-exact, underrun returns 0 (ring untouched), flush, and small-ring
  backpressure accounting (es_to_ring + es_dropped == video_es, prefix intact). LOOPBACK TCP SMOKE TEST
  PASSED: muxed the bars clip to a DVD-style PS (ffmpeg -f vob), served it over a real socket, ran netd
  -> output video ES is BYTE-FOR-BYTE identical to ffmpeg's own ES extraction (974x2048 sectors,
  dropped=0, prefix byte-exact). So the live PS-over-TCP path works end-to-end without the board. The
  ONLY board-specific remaining bit of deliverable (a) is swapping netd's drain_to_file() for the real
  sd_* service (one sd_rd request -> one ni_get_sector()). Decode-correctness HW-bisect still pending
  (next HW cycle).

- 2026-06-05 (caught up on chat Qs; audited 573's signed-multiplier lead — RTL signedness CORRECT,
  Altera gotchas HANDLED; localizing now needs the HW-bisect) — FIRST fixed a process miss: several
  @dvd chat questions weren't surfacing via `chat unread` (answered them: cockpit's UART legend, 573's
  diagnosis, the human's 'close to CRT?'). 573 gave a gold fingerprint: "DC/mean survives, AC detail
  killed -> gray attenuation = a SIGN bug in the iquant/IDCT multipliers (Quartus silently builds
  unsigned if operands aren't both signed; Verilator-invisible)." AUDITED it inline (no build):
  (1) IDCT butterfly (idct.v): operands cos1..7 + x0..7 + prod regs ALL `reg signed`, full-width
  products -> correctly signed. (2) Dequant (the real one is in rld.v:410, NOT iquant.v which is just
  the matrix RAMs): iquant_level_2 `signed[12:0]` * iquant_factor_2_signed `signed[15:0]` + signed
  correction, `>>>5` arithmetic shift, signed target -> correctly signed. (3) IDCT cos constants are
  `parameter`s (compile-time, HW-safe), not a ROM. Quartus log: the IDCT transpose RAMs
  (idct|transpose:col2row/row2col), rld RAMs, motcomp idct_dta_ram all got Warning 276020 "pass-through
  ADDED to match read-during-write" = RDW HANDLED (573's #1 suspect covered). Two RAMs "uninferred due
  to inappropriate RAM size" (zigzag_table.v:179, intra_quant_matrix) but zigzag is combinational
  `casex` (constants -> logic, no init/RDW) and the quant matrix loads via reset -> both HW-safe. ⇒
  the OBVIOUS Altera inference gotchas are NOT the bug. Remaining: a silent Quartus DSP unsigned-build
  despite signed RTL (only confirmable via the synth multiplier report or by forcing an explicit signed
  lpm_mult), OR something subtler. Static analysis EXHAUSTED -> the definitive next step is the HW-BISECT
  (uart-checksum iquant-OUT and IDCT-OUT separately per 573's plan) to empirically localize, and/or a
  cheap targeted build that forces explicit signed multiplies. Process fix: read full chat (not just
  `unread`) each cycle so questions don't get missed.

- 2026-06-05 (session relaunch after Mac update [CC 2.1.165]; BOTH loops re-armed; HW-BISECT probe
  LAUNCHED) — resumed unattended run on feat-decoder-bringup. (a) restarted the mission `/loop`
  (self-paced) AND the chat-poll loop (now an event-driven Monitor polling `dell_coord.sh chat unread
  dvd` @60s, exits/emits on a real @mention — cockpit independently verified this beats the old 5-min
  poll). Human authorized OVERNIGHT autonomy (don't park questions, ask peers via chat, smart
  roll-back-able commits). (b) Ran a 5-agent design workflow mapping the bisect: tap points = iquant-OUT
  (rld.iquant_level[11:0] signed, gated by iquant_valid) and IDCT-OUT (idct.idct_data[8:0] signed, gated
  by idct_valid), both in clk_sys 27MHz (same as uart_debug -> NO CDC). SHARPENED PRIME SUSPECT (beyond
  the prior multiplier audit): **idct.v:1409** in module `mult22x16` (the COLUMN pass) does
  `multiplier_msb = multiplier[21:4]` then `multiplier_msb * multiplicand`. multiplier_msb IS a `wire
  signed` so SIM computes it signed (correct -> sim decode is fine); the risk is QUARTUS inferring an
  UNSIGNED DSP from the part-select origin (573 case 2: silicon-only, Verilator-invisible) -> negative
  AC coeffs mangled -> gray. Row pass uses clean inline signed*signed. (c) CHECKSUM FORMULA (identical
  HW+sim, key: golden IS the sim's own value so HW-vs-sim divergence == silicon-only): 32-bit running sum
  of SIGN-EXTENDED valid-gated data (32-bit not 64 for wrap parity); never zero-extend (nets declared
  without `signed`). (d) SIM GOLDEN captured (agent, run_chksum/, hwclip md5 ab99abb5...):
  **QO=fffe8a38 IO=ffee0b8c QN=0018f200 IN=0018f140** (+ per-frame table in checksums.log); decode
  correct in sim (6 PPMs, geometry = hwclip 720x240i). Patch:
  core/patches/sim-iquant-idct-checksum-probe.patch (auto-applied by `make build`). (e) HW PROBE patch:
  core/patches/hw/mpeg2fpga-iquant-idct-checksum-uart.patch — mpeg2video.v 4 free-running accumulators +
  4 output ports; emu.sv 4 wires + connections to both instances; uart_debug.sv 4 inputs + snapshot regs
  + S_IDLE latch + new FSM fields QO:/IO: (8 nibbles) QN:/IN: (4 nibbles) at char_idx 161..202. Reverse-
  check OK (patch == working tree). (f) Synced rtl/ -> dell (diff-verified identical, per the :450 drift
  lesson; no drift), build slot 2 acquired (573 on slot 1 w/ debug-off build), LAUNCHED detached probe
  build (quartus-dvd). NEXT (build-done ~30-50min -> Monitor wakes loop): devlock mister, read UART
  QO/IO, compare to golden. QO-match+IO-mismatch => idct.v:1409 col-mult sign bug (apply held-back fix
  $signed(multiplier_msb)*$signed(multiplicand)); QO already off => iquant stage (rld.v:410 $signed fix).
  Held-back fixes kept SEPARATE so probe vs fix never confound.

- 2026-06-05 (BISECT RESULT: ROOT CAUSE FOUND = iquant unsigned-multiply; fix building) — probe build
  rc=0 (timing: only audio-PLL + a small general[1] aux clock fail; clk_sys/general[0] MEETS +0.722 so
  the checksums are valid). FIRST HW read showed an INHERITED f2sdram wedge (loaded without a warm
  reboot): QN=IN=0, W/P/RP frozen, watchdog every ~3s, only FC climbing = the wedge-vs-real-bug
  signature (saved as a tell). Warm-rebooted mister (standing-auth) -> CLEAN read: O:0 no watchdog,
  M:0/U:0 mem healthy, J=Z=0F3B full feed, checksums STABLE: HW QO=001244A2 IO=FFE9FB78 QN=IN=0x6500.
  Counts (0x6500=25856=block 404, since QN steps by 0x40=64/block) didn't match sim's 6-frame final, so
  COUNT-ALIGNED at block 404 via run_chksum/checksums.log: **iquant-OUT HW QO=001244A2 (+1,196,706) vs
  SIM QO=ffffcca2 (-13,150) -> DIVERGES, large-POSITIVE = unsigned-multiply SIGN-FLIP**. IDCT-OUT also
  diverges but downstream of the corrupt iquant (garbage-in). Verified rld.v + idct.v are IDENTICAL
  between the MiSTer_MPEG2 (HW) and mpeg2fpga (sim) trees -> bisect valid. ROOT CAUSE (rld.v:379/410):
  iquant_factor_2_signed = {1'b0, iquant_factor_2} and iquant_level_3_correction = {17'b0,...} are
  CONCATS -> UNSIGNED in Verilog regardless of the `wire signed` decl (IEEE 1364-2005 §5.5.1) -> Quartus
  inferred an UNSIGNED multiply, turning negative AC coeffs into huge positives = the gray-attenuation
  symptom. Verilator follows the `signed` wire so SIM is correct (golden valid). The port REGRESSED the
  original Xilinx code, which explicitly cast `$signed({1'b0, iquant_factor_2})` + `$signed({5{...}})`
  (still in rld.v:409 as a comment). FIX (core/patches/hw/mpeg2fpga-rld-iquant-signed-mult.patch):
  rld.v:410 -> `($signed(iquant_level_2) * $signed(iquant_factor_2_signed) + $signed(iquant_level_3_
  correction)) >>> 5` — casts ALL 3 operands so product, + and >>> (arithmetic shift) all stay signed on
  Quartus; no-op in Verilator so sim golden unchanged. (Cast all 3, not just the multiply: an unsigned
  correction term would make the + and the >>> logical, still corrupting negatives.) BONUS finding: HW
  decode STALLED at block 404 (~5% of frame 1) on the clean run -> the garbage coeffs likely choke
  downstream; the fix may un-stall it (watch QN climb past 0x6500). Concat-audit (573's tip) flagged
  more `wire signed = {..}` sites in motcomp_recon/motvec for future scrutiny if needed. Applied
  iquant-fix ONLY this build (rigorous: if IO ALSO matches sim after just the iquant fix, the IDCT was
  never broken -> no speculative idct.v:1409 change). Build (probe+fix) relaunched detached on dell.
  NEXT (build-done): warm-reboot mister FIRST (avoid inherited wedge), clean decode, read UART; expect
  HW QO==sim QO block-aligned + QN climbing past 0x6500 (decode proceeds). If QO matches but IO still
  diverges -> apply idct.v:1409 $signed fix next.

- 2026-06-05 (NEGATIVE RESULT: $signed iquant fix did NOT resolve the divergence; localizing within
  rld) — built+deployed the rld.v:410 $signed-all-3-operands fix (mpeg2fpga_dvd_probefix.rbf, rc=0),
  warm-rebooted mister (clean f2sdram, no wedge: O:0/M:0/U:0), clean decode. Count-aligned at block 16
  (QN=IN=0x400): **HW QO=000DF98E (+916,366) vs SIM QO=fffff2f8 (-3,336) — STILL DIVERGES** (HW large-
  positive, ~270x sim magnitude). QO VALUE changed from the no-fix build (001244A2 -> 000DF98E) so the
  fix WAS built+deployed (confirmed not stale) — it just didn't fix it. So the iquant-OUT corruption is
  NOT (solely) the rld.v:410 multiply signedness. Also: decode now stalls even earlier (block 16 vs 404
  pre-fix; full feed J=Z consumed both times) = the stall is downstream + data-dependent on the (still-
  garbage) coeffs. INVESTIGATION (no-build, RTL trace): (1) the quant matrix feeds via intra_quant_matrix
  (iquant.v) whose DEFAULT path (default_values=1) is a COMBINATIONAL function default_intra_quant(addr)
  -> HW-safe; default_values only clears after a full custom-matrix upload (wr_addr==0x3f). ffmpeg's
  stream uses the default matrix, so the matrix is likely NOT garbage UNLESS default_values spuriously
  clears on HW (-> reads the uninitialized custom-matrix RAM = garbage). (2) Quartus map.rpt multiplier
  summary after fix: 16 signed / 25 unsigned / 44 mixed-sign — can't isolate the rld:410 DSP from the
  summary, so whether $signed() actually forced THIS one signed is unconfirmed. DECISION: stop guessing
  fixes; add INTERMEDIATE iquant-stage UART checksums to localize EXACTLY where the divergence enters:
  iquant_factor_2 (the scale*matrix dequant factor), iquant_level_2 (multiply INPUT), iquant_level_3
  (multiply OUTPUT) — gated by iquant_valid_2/_3. Compare each to sim (testbench reads
  testbench.mpeg2.rld.* hierarchically). Verdicts: factor_2 diverges => matrix/scale path (incl. a
  spurious default_values clear or the unsigned scale*mat at rld.v:450); factor_2 ok + level_3 diverges
  => the multiply IS still unsigned despite $signed() (need explicit lpm_mult SIGNED); level_2 already
  diverges => upstream (VLD level / level_0 shift+add). $signed fix KEPT (harmless, restores original
  Xilinx intent, likely still needed once the real stage is found). HDMI/dashboard screenshot is black
  by design (raw-VGA core); board parked on menu between tests (the human saw the menu color-gradient
  screensaver, not decode). NEXT: build the intermediate-stage probe, localize, then targeted fix.

- 2026-06-05 (intermediate iquant-stage probe built; cheap Quartus-report checks RULED OUT 3 suspects;
  spine bug #1 fixed in parallel) — CHEAP localization (no build) from the fix build's reports on dell:
  (1) rld:410 multiply is in LOGIC (LEs), the ONLY rld DSP is Mult0 = iquant_factor_2 (the rld:450
  unsigned scale*mat) — so the rld:410 multiply is NOT a DSP signed/unsigned-inference issue (explains
  why $signed didn't help). (2) RDW warnings: rld un-zigzag dpram_sc ram0/ram1 AND both quant-matrix
  RAMs got Warning 276020 (pass-through ADDED = RDW HANDLED); only 276027 (undefined RDW) are dual-clock
  CDC FIFOs + the unused HDMI ascal/shadowmask. So RDW ruled out in the rld path. Remaining suspects:
  VLD input (level / vlc_tables ROM) or matrix/factor VALUES. Built the INTERMEDIATE probe (agent):
  32-bit UART checksums L2 = sum of iquant_level_2 (post-VLD multiply INPUT, sign-ext, gate
  iquant_valid_2) and F2 = sum of iquant_factor_2 (dequant factor, zero-ext, gate iquant_valid_2),
  accumulators in rld.v -> mpeg2video -> emu -> uart_debug fields L2:/F2: (char_idx 201-224, \r\n
  225/226), mirrored in sim testbench.v (testbench.mpeg2.rld.*). Patches:
  core/patches/hw/mpeg2fpga-iquant-stage-probe.patch + extended sim patch. VERIFIED before build:
  verilator lint of the MiSTer_MPEG2 mpeg2 core CLEAN (rld/mpeg2video port edits valid), uart FSM splice
  contiguous, identifiers cross-checked, rtl/ rsync'd to dell diff-verified. SIM GOLDEN (run_chksum,
  block 16 / N2=0x400): **L2=ffffec6a (negative) F2=00052940**; also block1 L2=fffffdde F2=00005294,
  block4 L2=fffff912 F2=00014a50. Build launched on dell (quartus-dvd). ON BUILD-DONE: warm-reboot
  mister (573 currently holds the devlock for its hyperbbc screenshot — wait for release), deploy, read
  UART, count-align L2/F2 vs sim at block 16. VERDICT: HW L2 diverges => VLD/upstream (vlc_tables ROM /
  level_0); L2 ok + F2 diverges => matrix/factor (default_intra_quant fn or default_values spurious
  clear); L2+F2 ok => the logic multiply/saturate (rld.v:411-415). PARALLEL (build-independent): fixed
  spine-review bug #1 = arm/ps_demux.c payload-0x00 drop on the one-chunk path (commit 3ff8d7d, Pass 6
  regression red/green verified). 573's hyperbbc booted from STOCK BIOS on HW (the human saw it on the CRT).

- 2026-06-05 (probe2 WEDGES = fit/timing tip [not board, not bug]; landing audio-IIR headroom to unblock;
  spine bug #2 fixed) — the intermediate L2/F2 probe build (probe2) WEDGED THE FEED DETERMINISTICALLY
  (5/5 attempts on clean warm reboots: J=0x23 ~190 writes, PC=A081, watchdog cycling, QN=0 = no decode).
  DIAGNOSIS: re-read the prior FIX build (probefix.rbf) -> CLEAN (full feed J=Z, real QO/IO, QN nonzero)
  => BOARD FINE; probe2's wedge is in the BITSTREAM = fit/timing perturbation (573's call, matching his
  patch-0009 experience: "the probe doesn't have to be WRONG, just PRESENT" near a fit margin). 573's
  3-reset-levels logic confirms: warm `reboot` re-inits the HPS DDR ctrl + f2sdram bridge (clears stale
  wedges), so a deterministic wedge from a fresh reboot is bitstream, not state -> no power-cycle needed.
  (Tooling note: my auto-retry clean-detector regex [1-9a-f] was case-sensitive and missed an uppercase
  QN=F600 -> use [1-9a-fA-F]/grep -i next time.) CHEAP CHECK ruled out the VLD vlc_tables ROM as the
  decode-bug cause: it's 24 combinational `case` statements (logic, not an init-dependent memory) ->
  HW-safe. So decode bug still localized to iquant-OUT, needs the L2 probe (VLD-logic vs iquant-arith)
  but the probe tips the marginal fit. UNBLOCK (573's audio-IIR-drop tweak): replaced the IIR_filter
  instance in sys/audio_out.v with a sign-preserving passthrough (assign acl/acr = {~is_signed^c[15],
  c[14:0]}) — frees ~8 DSP + ~430 ALM AND clears the unused audio-PLL timing crits; my audio_out.v
  matched 573's input expr EXACTLY (zero-boot-risk, verified by 573 on his core). Patch
  core/patches/hw/mpeg2fpga-audio-iir-drop.patch. Rebuilding probe2 + headroom on dell -> expect a
  non-wedging fit -> clean L2/F2 read. SIM GOLDEN block16: L2=ffffec6a F2=00052940. PARALLEL: fixed
  spine-review bug #2 = service/core/ps_demux.py MPEG-1 PES header leak (commit 7c26118, 5 regression
  tests red/green). NEXT: on build-done, clean L2/F2 read (count-align block16) -> targeted iquant fix.

- 2026-06-05 (probe3 STILL WEDGES with headroom — observer effect, NOT fit-size; escalated to 573;
  spine bug #3 fixed) — probe3 (probefix + L2/F2 probe + 573's audio-IIR drop; build rc=0, ~2.97MB rbf,
  freed 8 DSP + 430 ALM confirmed) STILL wedges the feed 5/5 (J~0x22, QN/L2/F2=0), identical to probe2.
  So the wedge is NOT pure fit-margin SIZE (430 freed ALM >> 2 accumulators). PUZZLE: the probe is all
  clk_sys (27MHz) — accumulators gated by iquant_valid_2 + 2 new rld output ports to uart — nowhere near
  the clk_mem (108MHz) f2sdram/mem_shim path that wedges. Yet probefix (4 accumulators, no L2/F2) feeds
  FULLY clean, and ADDING the L2/F2 taps deterministically wedges the f2sdram READ at ~sector 34. =>
  OBSERVER EFFECT (placement/routing relocating or starving the mem_shim path despite freed ALM), not a
  decode bug and not the board (probefix re-read clean = board fine). ESCALATED to 573/cockpit (573
  cracked similar fit-marginality in patch-0009). Candidate next: MINIMAL-perturbation retry = retarget
  ONE existing probefix accumulator from iquant_level to iquant_level_2 (keep count=4 = the clean fit
  profile) instead of ADDING 2 — pending 573 input. Decode bug remains localized to iquant-OUT (QO
  large-positive); the L2/F2 sub-stage localization (VLD-input vs matrix-factor vs saturate) is BLOCKED
  on this probe-wedge. PIVOT (autonomy playbook: blocked critical path -> escalate + advance unblocked):
  build-independent spine fixes. SIM GOLDEN block16 L2=ffffec6a F2=00052940 still valid for when a clean
  probe lands. Spine: 3 bugs fixed so far (arm ps_demux 3ff8d7d, python ps_demux 7c26118, ifo
  inverted-cell b2ba13f); continuing the contained ones.
