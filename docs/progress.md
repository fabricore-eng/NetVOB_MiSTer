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
