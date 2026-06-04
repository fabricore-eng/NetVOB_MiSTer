# NetVOB_MiSTer — Project Plan

> Play video on a CRT at native **480i with 24-bit color** by **hardware-decoding a
> DVD-like MPEG-2 stream inside an FPGA**. The name nods to DVD's Video Object
> (`.VOB`) container — the same MPEG-2 program-stream payload, streamed over the
> network instead of read off a disc.

**Target device:** SuperStation One — for build purposes the standard MiSTer Intel
Cyclone V **`5CSEBA6U23I7`** (DE10-Nano part; confirmed build target, see §3),
dual-core ARM Cortex-A9 @ 800 MHz HPS, 128 MB SDRAM, MiSTer-compatible, true
24-bit **ADV7125** analog DAC, outputting **480i component**.

**Status:** Planning. No HDL/implementation code yet. This document and the files
under [`docs/`](docs/) are the deliverable.

---

## 0. TL;DR and locked decisions

| # | Decision | Choice | Why |
|---|----------|--------|-----|
| D1 | FPGA decoder strategy | **Finish/adopt `mrchrisster/MiSTer_MPEG2`** (the existing Cyclone V port of the BSD `mpeg2fpga` decoder) | It already proves the exact ingest seam we need and is the only Cyclone V MPEG-2 port that exists. But it has **no confirmed video output yet** → "reach confirmed, stable video-out" is the gating early milestone, not an assumption. |
| D2 | Audio | **In scope; decoded on the ARM (HPS)** → MiSTer I2S/analog path, lip-synced via PTS | No prior-art MPEG-2 decoder handles audio; ARM-side AC-3/MP2 decode is vastly cheaper than fabric audio. |
| D3 | Wire transport | **MPEG-2 Program Stream over TCP** (single media protocol) + a small control channel | DVD VOB is *already* PS, so the dump spine is a lossless passthrough; TCP gives reliability so PS's loss-fragility is moot; ARM unwraps PS→ES. See [`docs/transport.md`](docs/transport.md). |
| D4 | Two libraries | **Separate, never merged/deduped**: "DVD Dumps (field-exact)" and "Plex (transcoded)" | Per spec; keeps each backend's quirks quarantined. |

**The single most important finding** (full detail in
[`docs/findings.md`](docs/findings.md)): the premise that *"decode is the done
80%"* does **not** survive verification. The decode **algorithm** exists as mature,
BSD-licensed, full-pipeline RTL (`mpeg2fpga`), and a Cyclone V **port attempt**
exists (`MiSTer_MPEG2`) whose **data path is hardware-verified** — but by its own
engineering notes that port has **no confirmed video output**, no audio, no A/V
sync, a hardcoded SD pixel clock, and no release. **The Cyclone V decoder bring-up
+ validation (field-exact 480i, A/V sync, audio) is the true center of gravity of
this project.** The architecture (compressed-over-network → FPGA decode → analog
out) is sound and is **not** relitigated here; only the *maturity of the prior art*
is corrected, because it reshapes the milestones and risk register.

---

## 1. Architecture (the spine)

A **source-agnostic MPEG-2 provider service** runs on the Raspberry Pi 5 (alongside
but independent of Plex). It owns the wire to the FPGA core and always emits **one
clean MPEG-2 Program Stream over TCP**, filled from pluggable backends behind a
common `Source` interface (`browse()` / `open(id)`). The core speaks only the one
protocol; each backend's quirks (Plex transcode endpoints, DVD demux) stay
quarantined in the service.

### 1.1 Data-flow diagram

```
┌──────────────────────── Raspberry Pi 5 — provider service ─────────────────────────┐
│                                                                                     │
│   ┌───────────────┐   ┌───────────────┐      (future, designed-for, not built)      │
│   │ DVDDumpSource │   │  PlexSource   │      ┌───────────┐ ┌──────────────┐         │
│   │ VOB/VIDEO_TS  │   │ Plex API  +   │      │ DiscSource│ │ SSDDumpSource│         │
│   │ → PS pass-    │   │ ffmpeg → MPEG-│      │ (SuperDock│ │ (SuperDock   │         │
│   │   through     │   │ 2 PS (480i)   │      │  DVD-RW)  │ │  NVMe)       │         │
│   └──────┬────────┘   └──────┬────────┘      └───────────┘ └──────────────┘         │
│          │  browse() / open(id)  ── common Source interface ──                       │
│          └─────────┬──────────┘                                                      │
│                    ▼                                                                 │
│        ┌────────────────────────┐     ┌───────────────────────────┐                 │
│        │ Service core            │◄──►│ Control channel             │                │
│        │ • owns the wire         │     │ browse / select / play /    │                │
│        │ • one protocol: PS      │     │ pause / seek / stop         │                │
│        │ • pre-buffer + pacing    │    └───────────────────────────┘                 │
│        └───────────┬────────────┘                                                    │
└────────────────────┼─────────────────────────────────────────────────────────────────┘
                     │  MPEG-2 Program Stream over TCP  (single media protocol)
                     │  + control channel (TCP/WebSocket)        [ wired GbE LAN ]
                     ▼
┌──────────────────────── SuperStation One — Cyclone V ──────────────────────────────┐
│  HPS (ARM Linux, dual A9)                         FPGA fabric                         │
│  ┌────────────────────────────┐                  ┌─────────────────────────────────┐ │
│  │ NetVOB ingest app           │                 │ MPEG-2 decoder core (fork of     │ │
│  │ • TCP recv + ring buffer    │   sd_* sector   │ MiSTer_MPEG2 / mpeg2fpga)        │ │
│  │ • PS demux → video ES        │  handshake     │ ┌─────────────────────────────┐  │ │
│  │ • audio ES → AC-3/MP2 decode │═══════════════►│ │ input FIFO (vbuf/getbits)    │  │ │
│  │   → PCM                      │ sd_lba/sd_rd/   │ └──────────────┬──────────────┘  │ │
│  │ • A/V sync via PTS           │ sd_ack/sd_buff_*│  VLD→IQuant→IDCT→MotionComp     │ │
│  │ • playback control           │                 │                │  (all fabric)   │ │
│  └──────────┬──────────────────┘                  │  frame store (DDR3 via f2sdram) │ │
│             │ PCM (I2S)                            │                ▼                 │ │
│             ▼                                      │  yuv2rgb (BT.601) + syncgen     │ │
│      MiSTer audio path → analog / HDMI audio       │  vblank-latched 24-bit RGB      │ │
│                                                    └────────────────┬────────────────┘ │
│                                            24-bit RGB + HS/VS/DE, dot_clk               │
│                                                                     ▼                   │
│                                                          ADV7125 24-bit DAC             │
└─────────────────────────────────────────────────────────────────────┼─────────────────┘
                                                                       ▼
                                              CRT — native 480i component, 24-bit color
```

### 1.2 Components

| Component | Runs on | Responsibility | Built from |
|-----------|---------|----------------|------------|
| **Source plugins** | Pi 5 | `browse()` catalog + `open(id)` → MPEG-2 PS | New (Python). See [`docs/service-design.md`](docs/service-design.md) |
| **Service core** | Pi 5 | Owns the wire; one PS-over-TCP protocol; pre-buffer; control channel | New |
| **NetVOB ingest app** | HPS (ARM) | TCP recv → PS demux → video ES into `sd_*` seam; audio decode → I2S; A/V sync; playback control | New, modeled on MiSTer `Main` sector-service code |
| **MPEG-2 decoder core** | FPGA | Decode video ES → 24-bit RGB raster, vblank-latched | **Fork of `MiSTer_MPEG2`** (wraps BSD `mpeg2fpga`) |
| **Catalog/browse UI** | **MiSTer OSD first**, then HPS framebuffer | Browse two separate libraries, badging, selection (incl. NFC/Zaparoo). **The core just plays video; the controller is the remote.** Ship in the OSD (go/no-go), then a **retro "alternate-history DVD player" Plex-style** UI + DVD-player transport/overlays/menus | New. See [`docs/catalog-browse.md`](docs/catalog-browse.md) (§3 phases, §8 playback) |

### 1.3 Key interfaces

1. **Source interface** (Pi): `browse() -> listing`, `open(id) -> MPEG-2 PS byte
   stream`, plus `capabilities`/`metadata`. Built to admit future console-local
   backends without rearchitecting. See [`docs/service-design.md`](docs/service-design.md).
2. **Wire protocol** (Pi ↔ ARM): MPEG-2 **Program Stream over TCP** as the media
   channel; a separate **control channel** (TCP/WebSocket JSON) for
   browse/select/play/pause/seek/stop. See [`docs/transport.md`](docs/transport.md).
3. **ARM ↔ FPGA seam**: the **`sd_*` CD-sector handshake**
   (`sd_lba`/`sd_rd`/`sd_ack`/`sd_buff_dout`/`sd_buff_wr`) in `hps_io.sv` — the ARM
   services sector requests from the network ring buffer instead of a local image.
   This is exactly what `MiSTer_MPEG2`'s `mpg_streamer.sv` already does and what the
   CD-i core uses (`hps_cd_sector_cache.sv`). See [`docs/findings.md`](docs/findings.md).

---

## 2. Scope

**PRIMARY (this project):** two **network** source plugins, surfaced on the core as
**entirely separate libraries** (no dedupe, no merging):

1. **DVDDumpSource** — reads VOB/VIDEO_TS dumps and **passes the program stream
   through** (PS on the wire = the VOB's own payload). Lossless, field-exact,
   near-zero CPU. **The spine.**
2. **PlexSource** — queries the Plex API, pulls the file, **transcodes to 480i
   MPEG-2** PS on the Pi (`ffmpeg`). Lossy and CPU-heavy; covers content that was
   never a disc.

**FUTURE TIER (design for, do NOT build):** the `Source` interface must admit these
later without rearchitecting:

- **DiscSource** — live DVD from a SuperDock DVD-RW drive, read/remuxed **locally on
  the console ARM**.
- **SSDDumpSource** — VOB dumps on the SuperDock NVMe SSD, read locally.

**OUT OF SCOPE entirely:** SuperDock "PC Mode."

**Parked open question (do not resolve now):** whether the SuperStation firmware
exposes the SuperDock optical drive as a generic OS block device (disc loading for
PS1/Saturn is done via per-system firmware work). Publicly undocumented as of
mid-2026. Tracked as a **future** risk, not a current blocker.

---

## 3. Hardware context (verified)

- **SuperStation One:** **build target = `5CSEBA6U23I7`** (standard MiSTer/DE10-Nano
  Cyclone V part, 672-pin, sg7 — *confirmed*: the other project's stock-`sys/` build
  targets it and runs on the SuperStation), dual A9 @ 800 MHz, ~110K LE, **128 MB
  SDRAM**, runs **stock MiSTer**. Analog out via **ADV7125** (triple 8-bit = true
  24-bit RGB/component DAC). Built-in **NFC/Zaparoo** reader, Wi-Fi/BT, dual PS1 SNAC.
  Built by Retro Remake (Taki Udon). *480i is a property of the core's video timing
  driving the DAC, not of the DAC itself.* (For build purposes the SuperStation **is**
  the standard MiSTer target — stock Template_MiSTer `sys/`, DE10-Nano interchangeable;
  any difference is at most board-level pins. Tech-press's `5CSXFC6D6F31I7N` is
  contradicted by the working build — unreliable. See
  [`docs/dev-workflow.md`](docs/dev-workflow.md) §0.)
- **Raspberry Pi 5:** quad Cortex-A76 ~2.4 GHz, BCM2712. **HEVC 4Kp60 hardware
  decode only**; **no** hardware H.264 decode, **no** hardware encode, **no** MPEG-2
  hardware. → **MPEG-2 encode is software** (`ffmpeg mpeg2video`); one SD stream
  fits the A76 budget easily *alone*, but contends with concurrent Plex transcoding.
  Plan for active cooling.
- **Memory reality on the FPGA side:** the MiSTer framebuffer/CMA region is tight
  (the `MiSTer_MPEG2` port packs a 15.5 MB HD frame buffer into the **24 MB CMA**).
  SD/480i frame stores are far smaller (the decoder's SD mapping needs ~4 MB), so SD
  is comfortable.

---

## 4. Proposed repository structure

Root: `NetVOB_MiSTer/`

```
NetVOB_MiSTer/
├── README.md                 # one-paragraph intro + pointers
├── PLAN.md                   # this file — architecture, decisions, data flow
├── CLAUDE.md                 # guidance for future Claude/dev sessions
├── .env.example              # session secrets/config template (copy → .env, gitignored)
├── .gitignore                # ignores .env, build/sim artifacts, large test media
├── .claude/
│   └── hooks/session-start.sh  # SessionStart hook wrapper (register in settings.json to enable)
├── scripts/
│   └── verify-session.sh     # prerequisite verifier — prints reachable/blocked report
├── docs/
│   ├── findings.md           # VERIFIED prior art + the injection seam (with citations)
│   ├── service-design.md     # Pi 5 provider service + Source plugin interface
│   ├── transport.md          # PS-over-TCP decision; buffering / flow-control / A-V sync
│   ├── catalog-browse.md     # two-library UI, badging, NFC/Zaparoo, metadata/disc-ID
│   ├── milestones.md         # M0–M7 (+ future tier)
│   ├── risk-register.md      # ranked risks + mitigations
│   ├── dev-workflow.md       # build / sim / HPS data paths / hardware bring-up
│   ├── session-bootstrap.md  # AUTONOMY: what to stage up front (secrets/assets/hosts)
│   └── autonomy.md           # AUTONOMY: the unattended run-loop (ultracode/multi-agent)
├── service/                  # Pi 5 provider service (code lands post-planning)
│   ├── README.md
│   ├── core/                 #   protocol server (PS-over-TCP), pre-buffer, control channel
│   └── sources/              #   pluggable backends behind the Source interface
│       ├── base/             #     Source ABC + shared helpers
│       ├── dvddump/          #     DVDDumpSource (VOB/VIDEO_TS → PS passthrough)
│       └── plex/             #     PlexSource (Plex API + ffmpeg → MPEG-2 PS)
│       #   future: disc/ (SuperDock DVD-RW), ssddump/ (NVMe)
├── core/                     # FPGA core — fork of MiSTer_MPEG2 (RTL lands later)
│   └── README.md             #   upstream pointers + integration/port notes
├── arm/                      # HPS-side NetVOB ingest app (network → sd_* seam, audio, sync)
│   └── README.md
└── tools/                    # encode/remux helpers, disc-ID, test-stream generation
    └── README.md
```

Rationale: the three runtime domains (Pi `service/`, FPGA `core/`, ARM `arm/`) are
top-level and independently buildable; `sources/` mirrors the plugin boundary so a
future backend is "add a directory," never a refactor. `tools/` holds the test
harness (canned PS streams, disc-ID utility) that de-risks the early milestones.

---

## 5. Milestones (summary)

Full detail, exit criteria, and per-milestone risk in
[`docs/milestones.md`](docs/milestones.md).

- **M0** — Reproduce existing VCD/MPEG playback (CD-i core VCD baseline; stand up the
  Quartus toolchain; attempt to reproduce `MiSTer_MPEG2`'s "prior working config").
- **M1** — **Get `MiSTer_MPEG2` to confirmed, stable video-out**, then feed it a
  **file-based MPEG-2 PS via the ARM** over the `sd_*` seam. *(The gating de-risk.)*
- **M2** — Live network ingest: PS-over-TCP from the Pi into the seam; basic play.
- **M3** — **DVDDumpSource** end-to-end (the lossless spine).
- **M4** — Catalog/browse: two-library listing + selection (incl. NFC/Zaparoo).
- **M5** — **PlexSource** as the second library.
- **M6** — Playback controls + seek + A/V-sync hardening (ARM audio decode lip-synced).
- **M7** — 480i / 24-bit / field-cadence polish (true field-exact output).
- **Future (out of scope now):** DiscSource + SSDDumpSource (console-local).

---

## 6. Document index

| Deliverable | File |
|-------------|------|
| Architecture + data flow + components + interfaces | this file |
| Verified prior-art findings + injection seam | [`docs/findings.md`](docs/findings.md) |
| Pi 5 service + Source plugin interface + two plugins | [`docs/service-design.md`](docs/service-design.md) |
| Transport decision + buffering/flow-control/A-V sync | [`docs/transport.md`](docs/transport.md) |
| Catalog/browse + badging + NFC + metadata/disc-ID | [`docs/catalog-browse.md`](docs/catalog-browse.md) |
| Milestone plan M0–M7 | [`docs/milestones.md`](docs/milestones.md) |
| Risk register | [`docs/risk-register.md`](docs/risk-register.md) |
| Build / sim / HPS data paths / hardware bring-up | [`docs/dev-workflow.md`](docs/dev-workflow.md) |
| Autonomy — prerequisites to stage up front | [`docs/session-bootstrap.md`](docs/session-bootstrap.md) |
| Autonomy — unattended run-loop (ultracode/multi-agent) | [`docs/autonomy.md`](docs/autonomy.md) |
