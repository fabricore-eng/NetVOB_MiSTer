# CLAUDE.md — guidance for AI/dev sessions in this repo

## What this project is

**NetVOB_MiSTer** plays video on a CRT at native **480i, 24-bit color** by
**hardware-decoding a DVD-like MPEG-2 program stream inside an FPGA**. A
source-agnostic provider service on a **Raspberry Pi 5** streams **one MPEG-2
Program Stream over TCP** to a **SuperStation One** (Cyclone V, MiSTer-compatible);
the console ARM injects it into an FPGA MPEG-2 decoder core; output goes out the
true 24-bit **ADV7125** analog DAC at 480i.

The name nods to DVD's Video Object (`.VOB`) container — the same MPEG-2
program-stream payload, streamed over the network instead of read off a disc.

**Current phase: PLANNING → IMPLEMENTATION (authorized).** The planning deliverable
(`PLAN.md` + `docs/`) is complete. The user has authorized building the full system,
so **HDL and implementation code are now in scope**, driven decoder-first down the
milestone ladder ([`docs/milestones.md`](docs/milestones.md)). Implementation runs as an
unattended **`/loop`** on the `feat-decoder-bringup` branch — read
[`docs/autonomy.md`](docs/autonomy.md) §0 (the kickoff) **first**, and honor its
**pre-flight: request every missing prerequisite (per
[`docs/session-bootstrap.md`](docs/session-bootstrap.md)) before any build/HW work.**
Start at [`PLAN.md`](PLAN.md).

## Ground rules

1. **Do not relitigate the architecture.** "Compressed video over the network →
   decode in FPGA fabric → analog out locally" is validated and settled. The analog
   path is 24-bit; the DVD-dump path preserves true field cadence. Don't re-argue
   it.
2. **Do challenge prior-art *maturity* with evidence.** The user explicitly wants
   unverifiable claims flagged. The big one: *"decode is the done 80%"* is **not**
   true — see below. Always verify against the actual repos and cite sources.
3. **Two libraries stay separate.** "DVD Dumps" and "Plex" are never merged or
   deduped. Each backend's quirks stay quarantined inside its Pi-side plugin.
4. **Future tier is design-only.** DiscSource (SuperDock DVD-RW) and SSDDumpSource
   (NVMe) must be *admittable* by the `Source` interface but are **not** built.
   SuperDock "PC Mode" is fully out of scope. The SuperDock-optical-as-OS-block-
   device question is **parked**, not a blocker.

## The single most important fact

The MPEG-2 **decode algorithm** exists as mature, **BSD-licensed**, full-pipeline
RTL: **`mpeg2fpga`** by Koen De Vleeschauwer (OpenCores; GitHub mirror
`OldRepoPreservation/mpeg2fpga`). But it is a **2007 Xilinx Virtex-5** design.

A Cyclone V / MiSTer **port attempt** exists: **`mrchrisster/MiSTer_MPEG2`**. Its
data path (the `sd_*`-streaming `mpg_streamer.sv`) is **hardware-verified loading
files**, but by its own engineering notes it has **NO confirmed video output**, FSM
hangs, a hardcoded 27 MHz SD pixel clock, **no audio, no A/V sync, no release, and
zero independent verification.**

→ **Treat the Cyclone V decoder bring-up + validation as the project's center of
gravity, not a solved 80%.** Decision D1: build on / finish `MiSTer_MPEG2`.

## Key external repos (verify, don't trust blindly)

| Repo | Role | Notes |
|------|------|-------|
| `OldRepoPreservation/mpeg2fpga` (+ OpenCores) | The MPEG-2 decoder RTL | BSD; MP@ML; input `stream_data[7:0]`+`stream_valid`; output RGB/YCbCr raster; needs external RAM; **supports interlaced output**. Xilinx-origin. |
| `mrchrisster/MiSTer_MPEG2` | Cyclone V port we fork | `mpg_streamer.sv` (`sd_*` seam, verified loading), `mem_shim.sv` (DDR3/24 MB CMA @ `0x30000000`). **No confirmed video yet.** |
| `MiSTer-devel/CDi_MiSTer` (upstream `Slamy/CDi_MiSTer`) | Best *flow* template | MPEG-**1** FMV ("VMPEG"/DVC) lives in `rtl/mpeg/` on `main` (**no "DVC branch"**). Hybrid RISC-V softcore + HW; progressive SIF. Maps demux→FIFO→decode→BT.601→vblank→plane-mux→`VGA_*`. |
| `mrchrisster/mister_cdi_vcd_creator` | Mux-side reference | Produces **MPEG-1 VCD** (despite "MPEG-2" wording): `ffmpeg → mpeg2enc -f 1 → mplex -f 1 → vcdimager`. |
| `MiSTer-devel/Main_MiSTer`, `Template_MiSTer` | Framework | `hps_io.sv` (ioctl + `sd_*`), `user_io.cpp` (sector service, `cdi_read_cd`), `fpga_io.cpp` (bridges/`mmap`). |

`mrchrisster` is the common upstream for both the VCD creator and the MPEG-2 port —
**realistically a key collaborator**, not just a code source. Coordinate upstream.

## The injection seam (deliverable (a))

Inject the live stream where the **disc-image source feeds the decoder**: the
**`sd_*` CD-sector handshake** in `hps_io.sv` (`sd_lba`/`sd_rd`/`sd_ack`/
`sd_buff_dout`/`sd_buff_wr`). The ARM services sector requests from a **network ring
buffer** instead of a local image — minimal RTL change, built-in pacing (the core
pulls only as its decoder drains). The compressed bitrate (~6–15 Mbps ≈ <2 MB/s) is
far below even the SPI-limited sector path's throughput, so bandwidth is a
non-issue. Ranked alternatives (DDR ring, `ioctl`+`ioctl_wait`) in
[`docs/findings.md`](docs/findings.md).

## Conventions

- **Markdown deliverables** under `docs/`. Keep claims cited; tag uncertain ones.
- When you state a hardware/RTL fact, point to the file/signal (e.g.
  `hps_io.sv: sd_buff_wr`) so it's checkable.
- Pi service language: **Python** (orchestration + `ffmpeg` subprocess +
  `libdvdread`/`dvdnav`); the DVD remux is byte-copy, not CPU-bound.
- ARM ingest app: C/C++ modeled on `Main_MiSTer`'s sector-service code, or a
  userspace daemon that feeds the existing mechanism.
- Don't invent module/signal names — use the verified ones or mark them as proposed.

## Build/test workflow

Full operational playbook (carried over from a sibling MiSTer core, adapted):
[`docs/dev-workflow.md`](docs/dev-workflow.md). Key facts:

- **Build target:** the standard MiSTer device **`5CSEBA6U23I7`** (DE10-Nano part) with
  the stock Template_MiSTer `sys/` — *confirmed* to compile and run on the SuperStation
  (the user's other project does exactly this). The SuperStation IS the standard MiSTer
  target; a DE10-Nano is interchangeable. (Tech-press's `5CSXFC6D6F31I7N` is contradicted
  by the working build; an earlier incompatibility concern was retracted — see
  `docs/dev-workflow.md` §0.)
- **FPGA build:** Quartus Prime Lite **17.0.x** (x86-only); use Docker
  `raetro/quartus:17.0`; launch remote builds **detached** (`setsid nohup … &`);
  ~30 min, single-thread-bound. `TOP_LEVEL_ENTITY sys_top`; `sys.tcl` sets the DEVICE.
- **Sim first (the #1 de-risk):** `mpeg2fpga` is Verilog, CD-i is SystemVerilog →
  **Verilator**. Reuse `mpeg2fpga`'s `bench/conformance` and **dump a framebuffer PNG**
  to validate decode (incl. interlaced) *before* building a bitstream. One writer per
  trace dir; never claim a milestone from a single run.
- **Hardware loop:** `ssh mister`; `/dev/MiSTer_cmd` (`load_core`, `Mount`,
  `screenshot`). For this `sd_*`-streaming core, hands-off test = **`Mount` a vdisk
  with a test clip** (not `.mra`/ioctl); verify with a **filmstrip** burst, not single
  shots.
- **Vendoring:** `core/` vendors `MiSTer_MPEG2` + MiSTer `sys/` + the CD-i core as
  **submodules pinned to a SHA**, with our edits as **re-appliable patch files**.
- **Video out:** drive the decoder's raster **`VGA_*`** for field-exact 480i → ADV7125;
  `FB_*` DDR framebuffer is the fallback/HDMI path.
- **Pi service:** Python project under `service/` (no code yet).
- A **SessionStart hook** to verify prerequisites is **scaffolded**:
  `scripts/verify-session.sh` (+ the `.claude/hooks/session-start.sh` wrapper) prints a
  reachability/asset report (build box, `ssh mister`, Verilator/ffmpeg, `.env`, dumps,
  submodule pins). Register it in `.claude/settings.json` to auto-run. See
  [`docs/session-bootstrap.md`](docs/session-bootstrap.md).

## Autonomy & session bootstrap

This repo is set up for **long unattended runs** by a Claude Code (web) session. Two docs
govern it:

- [`docs/session-bootstrap.md`](docs/session-bootstrap.md) — **prerequisites**: every
  secret/asset that must be staged up front because the agent can't fetch it (SSH to the
  SuperStation/Pi/an x86 **build box**, `.env` creds, DVD test dumps, the network policy).
  The cloud container **can't run Quartus or reach the LAN by itself** — provision a
  reachable build box; otherwise the run is productively **sim-only**.
- [`docs/autonomy.md`](docs/autonomy.md) — the **run-loop**: run high-effort
  (**ultracode**) mode + **multi-agent orchestration**; advance every FPGA-independent
  track in parallel (Pi service, ARM logic, `tools/`, **decoder sim**); launch Quartus
  builds **detached** and sims in **background** (never block); **checkpoint via frequent
  commits/push** (ephemeral container); climb the verification ladder (sim PNG →
  filmstrip), one writer per trace dir, **reproduce before claiming**.
- **Kick off** an unattended run with a single **`/loop`** (self-paced) — the canonical
  invocation is in [`docs/autonomy.md`](docs/autonomy.md) §0: *continue the core mission →
  **request missing prerequisites first** → full autonomy on `<feat-branch>` (never
  `main`) → checkpoint each cycle to git + [`docs/progress.md`](docs/progress.md) → effort
  ultracode*.
- Read the `scripts/verify-session.sh` report **first** each session to know which
  workstreams are unblocked.

## Git

- Develop on the designated feature branch; commit with clear messages; push with
  `git push -u origin <branch>`. Do **not** open a PR unless explicitly asked.
