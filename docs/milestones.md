# Milestone plan (M0–M7)

Sequenced to **de-risk the decoder first** (the verified center of gravity), then
build outward along the spine. Each milestone has a **goal**, **exit criteria**, the
**hardware** it needs, and its **dominant risk** (full register in
[`risk-register.md`](risk-register.md)).

> The biggest deviation from the original sketch: **M0–M1 explicitly include getting
> `MiSTer_MPEG2` to confirmed, stable video output.** The prior art does *not* give
> us a working Cyclone V MPEG-2 decoder for free (see [`findings.md`](findings.md)
> §1, §4), so "confirmed video-out" is a gate, not an assumption.

---

## M0 — Reproduce existing VCD/MPEG playback (baseline + toolchain)
**Goal:** stand up the build/sim/test environment and reproduce *known* playback so we
have a working reference and a regression baseline. (See
[`dev-workflow.md`](dev-workflow.md) for the how.)
- **Stage session prerequisites** ([`session-bootstrap.md`](session-bootstrap.md)): fill
  `.env` (SSH to the SuperStation/Pi/x86 build box, Plex/TMDB creds), stage DVD test
  dumps, set the network policy; register the SessionStart hook (`.claude/settings.json` →
  `scripts/verify-session.sh`) so each session opens with a reachable/blocked report.
- **Reuse the existing SuperStation build flow** from the other project (it already
  compiles and runs cores on the SuperStation — no porting/board-file blocker). Record
  its Quartus **DEVICE** target for reproducibility. See
  [`dev-workflow.md`](dev-workflow.md) §0.
- **Toolchain:** Quartus 17.0.x via Docker `raetro/quartus:17.0`, detached remote
  builds + log dashboard; **Verilator** sim; `ssh mister` + `/dev/MiSTer_cmd`
  (`load_core`/`Mount`/`screenshot`) + a **filmstrip** burst tool.
- **Vendoring:** set up `core/` with `MiSTer_MPEG2` + MiSTer `sys/` + the CD-i core as
  **submodules pinned to a SHA**, edits as re-appliable patches.
- Build & run the **CD-i core** and play an `mister_cdi_vcd_creator` VCD (MPEG-1) —
  confirms the toolchain, the MiSTer video-out path, and a real MPEG decode end-to-end.
- **Sim baseline:** get `mpeg2fpga`'s `bench/conformance` running under Verilator and
  **dump a decoded-frame PNG** — establishes the "see a frame before a bitstream" rung.
- Clone **`mrchrisster/MiSTer_MPEG2`**; attempt to reproduce its referenced "prior
  working config that produced video." Document what builds, what hangs.
- Reach out to **mrchrisster / Slamy** (upstream) re: current state and collaboration.

**Exit:** Quartus builds a core for the SuperStation (existing flow); CD-i VCD plays on
hardware (verified by filmstrip); the `mpeg2fpga` conformance sim emits a correct
decoded-frame PNG; a documented, reproducible `MiSTer_MPEG2` build with notes.
**Hardware:** SuperStation One; SD card; CRT/display. **Risk:** toolchain/core-template
constraints.

## M1 — Confirmed MPEG-2 video-out, then file → decoder via the ARM (the seam) ⟵ *gating*
**Goal:** the de-risk gate — prove the FPGA actually **decodes MPEG-2 to correct
video**, then prove the **ARM can feed it** over the `sd_*` seam.
- **M1a:** get `MiSTer_MPEG2` to **confirmed, stable video output**. *Sim first:* run
  the full decoder in Verilator on a known clip and **dump a correct decoded-frame
  PNG** (cheap, no 30-min bitstream) — then build and confirm on hardware from an
  on-board `.mpg` (resolve the `mem_shim`/DDR3-CMA hangs; one-PLL clocking), verified
  by **filmstrip**. *The single most important task in the project.*
- **M1b:** drive the decoder from a **file-based MPEG-2 PS supplied by an ARM
  userspace app** through the `sd_*` sector service (the seam). Hands-off hardware
  test: **`Mount` a vdisk** containing the clip (this exercises the production
  sector path; M2 only swaps the image for the network ring buffer). Validate against
  the `mpg_streamer.sv` hardware-verified loading path.

**Exit:** the decoder emits a correct frame **in sim (PNG)**; then a known 480i MPEG-2
PS clip plays on hardware, fed by an ARM process via `sd_*`, recognizable/stable/
full-color (filmstrip-verified, reproduced — not a single lucky run).
**Hardware:** SuperStation (and/or DE10-Nano). **Risk:** decoder bring-up (#1); RTL
seam wiring (low — proven pattern).

## M2 — Live network ingest, basic play
**Goal:** replace the ARM's local file with the **network**.
- ARM ingest app: **TCP client** receiving **PS over TCP** from a stub Pi server →
  ring buffer → PS demux → video ES → `sd_*` seam.
- Stub Pi server streams a canned PS file (from `tools/`). Basic `play`/`stop` over
  the control channel.
- Video-first acceptable here; audio path stubbed.

**Exit:** a clip streams Pi→console over the LAN and plays; decoder paces via pull;
no buffer under/overrun at steady state. **Hardware:** SuperStation + Pi 5 + LAN.
**Risk:** flow-control/backpressure correctness; clock-domain CDC at the seam.

## M3 — DVDDumpSource end-to-end (the lossless spine)
**Goal:** first real source; prove **field-exact lossless** path.
- Implement **DVDDumpSource**: IFO parse → title enumeration → PGC/VOBU read →
  **nav-pack-stripped PS passthrough** via `open(id)`.
- Play a real title from your dumps, Pi→console, lossless. Verify field-exactness as
  far as the current decoder output allows (full field-cadence polish is M7).

**Exit:** pick a dumped DVD title in a minimal list and watch it play, field-exact,
near-zero Pi CPU. **Hardware:** SuperStation + Pi 5 + your dumps. **Risk:** VOBU nav
correctness; field-cadence (partial; finished M7).

## M4 — Catalog/browse + two-library listing + selection (incl. NFC/Zaparoo)
**Goal:** make it usable without a keyboard.
- Catalog aggregator with **two separate libraries**; **badging**; control-channel
  `browse`.
- **Browse in the MiSTer OSD first** — *gated on an early go/no-go*: can the stock OSD/
  file-picker navigate a nested, multi-library catalog (sources as folders, titles as
  entries) acceptably? If yes, that's the **zero-custom-UI MVP** browser; if the OSD
  can't carry basic library navigation, fall through to the phase-2 browser (M8). See
  [`catalog-browse.md`](catalog-browse.md) §3.
- **Disc-ID metadata** for dumps (fingerprint → lookup → TMDB; sidecar/folder
  fallbacks; local cache).
- **NFC/Zaparoo:** tag → `{source, id}` → `play`. Confirm Zaparoo launch surface.

**Exit:** browse "DVD Dumps" as named titles **in the OSD** (or, if the go/no-go fails,
the minimal phase-2 list) and pick one to play; tap an NFC tag to launch one.
**Hardware:** + NFC tags. **Risk:** **OSD library-navigation feasibility (the go/no-go)**;
Zaparoo integration; disc-ID match quality.

## M5 — PlexSource as the second library
**Goal:** add the transcoded library.
- **PlexSource:** Plex API browse (posters/metadata) → catalog; `open(id)` pulls the
  original and **`ffmpeg` transcodes to 480i MPEG-2 PS** on the Pi.
- Surfaced as the **separate "Plex" library** with the `transcoded` badge.
- Validate Pi CPU **alone** and **under concurrent Plex transcode** (contention flag).

**Exit:** browse + play a Plex item transcoded to 480i, listed separately from dumps.
**Hardware:** + Plex server. **Risk:** Plex private-API stability; transcode CPU
contention.

## M6 — Playback controls + seek + A/V-sync hardening
**Goal:** real playback UX + lip-sync — the **controller becomes the DVD remote**.
- **Controller→transport mapping** (`catalog-browse.md` §8a): play/pause, stop, seek
  (scrub), chapter prev/next → control-channel messages; latency-sensitive actions may
  be handled console/ARM-side for instant feel.
- `pause`/`resume`/`seek{t}`; **GOP/VOBU-aligned seek** with buffer flush + decoder
  I-frame reset (both sources).
- **Transport overlay (MVP):** show play-state/chapter/time as a DVD-player-style status
  bar via the **MiSTer OSD** (the authentic core-overlay-plane version is M8, §8b).
- **Audio on the ARM:** AC-3/MP2 decode → PCM → I2S; **slave audio to the video
  presentation clock** via PTS (resample slew + post-seek resync). Settle the video
  clock reference (core-exposed vblank/frame counter).

**Exit:** the pad drives play/pause/seek/chapter; seek lands cleanly on a keyframe with
correct field parity; a transport readout shows on screen; audio stays in lip-sync
through play/seek/underrun on both libraries. **Hardware:** as M5. **Risk:** A/V sync over
network; seek across GOP; possible small RTL add for a vblank tick.

## M7 — 480i / 24-bit / field-cadence polish (true field-exact)
**Goal:** the headline quality bar.
- Lock **native 480i** output timing into the ADV7125 (correct field rate, parity,
  porches); verify **24-bit** end-to-end (no truncation/dither).
- **True field-exact / film cadence:** ensure interlaced field pictures and **3:2
  pulldown** are reproduced in original cadence (decoder field order + display
  timing + seek field-parity all consistent). For PlexSource, choose encoder field
  settings that match 480i; for DVDDumpSource, preserve the disc's native cadence.
- Replace `MiSTer_MPEG2`'s hardcoded 27 MHz SD assumption with correct, validated
  480i modeline(s).

**Exit:** a CRT shows native 480i, 24-bit, field-exact playback from the DVD spine;
PlexSource looks correct at 480i. **Hardware:** SuperStation + CRT + (ideally) a
capture/scope for field-timing verification. **Risk:** field-cadence preservation
(#3); modeline/DAC timing.

## M8 — The alternate-history DVD-player experience (post-spine, the polish vision)
**Goal:** the headline *feel* — NetVOB as a DVD player from a parallel timeline that
natively browsed Plex/network libraries. Everything here rides on a **working spine
(M1–M7)** and is large enough to rescope independently. See
[`catalog-browse.md`](catalog-browse.md) §3 (phase 2) + §8.
- **Retro-DVD-player Plex-style browser:** console-side framebuffer UI on the
  control-channel `browse` data — postered/badged lists in the early-2000s set-top
  on-screen idiom, CRT-native (480i-safe fonts, title-safe margins). Replaces/upgrades
  the M4 OSD browser.
- **Core overlay plane:** a small text/graphics layer composited over the decoded raster
  (à la CD-i plane-mux) for authentic translucent transport bars + UI (vs. the boxy OSD).
- **DVD features & menus (DVD-Dumps only):** dvdnav-style navigation — Pi runs the DVD
  VM (`libdvdnav`: menu PGCs, button highlight, nav commands), menu video through the
  *same* decoder; **subpicture (RLE) decode + alpha composite** into the overlay plane;
  controller → DVD button nav. Subtitle/audio/angle selection where streams carry them.

**Exit:** browse a postered retro UI, drive a real DVD's menus with the controller, and
see DVD-player-style overlays — all CRT-native. **Hardware:** SuperStation + CRT + pad.
**Risk:** subpicture decode/overlay is a **new path** (not in `mpeg2fpga`); DVD VM
fidelity; overlay-plane RTL fit on a near-full design.

---

## Future tier (OUT OF SCOPE now — design admits them)
- **DiscSource** — live DVD from SuperDock DVD-RW, read/remuxed on the console ARM.
- **SSDDumpSource** — VOB dumps on SuperDock NVMe, read locally.
- Gated on owning a SuperDock and on the **parked** question of optical-as-OS-block-
  device exposure. Neither blocks M0–M7; both are `Source` implementations behind the
  existing interface.

## Dependency notes
- **M1a gates everything** — if the decoder can't be made to output confirmed video,
  the project rescopes (see risk #1 contingencies). Tackle it first and hard.
- M2–M3 depend on M1; M4 depends on M3 (needs something to list); M5 depends on M4's
  catalog UI; M6 depends on M3/M5 (real streams); M7 hardens what M1–M6 produced.
- **M4 ships browse in the MiSTer OSD first** (go/no-go on whether the OSD can navigate
  libraries); **M8** is the post-spine experience tier (retro framebuffer browser + core
  overlay plane + DVD menus/subpicture) and depends on the **whole** spine (M1–M7). If
  the M4 OSD go/no-go fails, the *minimal* browser slips earlier from M8, but the rich
  experience stays post-spine.
- Audio (D2) physically lands in **M6**, but stub the audio ES split in **M2** so the
  PS demux is audio-aware from the start.
