# Dev workflow — build, sim, HPS data paths, hardware bring-up

Operational knowledge carried over from a sibling MiSTer core project (a Konami
System 573 arcade core on DE10-Nano), **adapted to this in-fabric MPEG-2 video
core**. The arcade specifics don't transfer; the build/sim/streaming/verification
patterns do. Where this core differs from the sibling, it's called out **⚠️**.

> Still planning — no build is wired up yet. This is the playbook to stand up in
> **M0** ([`milestones.md`](milestones.md)).

---

## 0. Build target — reuse the existing SuperStation flow

The other project **already compiles MiSTer cores and runs them on the SuperStation
One**, so that proven flow is our flow. There is **no portability problem to solve and
no DE10-Nano required** — M0 just stands our core up in the existing setup (Quartus
17.0.x, the `sys/` it uses, the deploy path).

- One detail worth **recording** (not a blocker): a compiled `.rbf` is built for a
  specific FPGA device, so note the working build's Quartus **DEVICE** target — the
  `.qsf`/`sys.tcl` line, or whether a SuperStation-specific `sys/` is used. Most likely
  it's the standard MiSTer target (`5CSEBA6U23I7`), in which case the same `.rbf` also
  runs on a DE10-Nano; if instead it's a SuperStation-specific `sys/`, you already have
  it. We set ours to match and build for the SuperStation directly.
- A DE10-Nano remains **optional** (a handy second test rig), not needed.
- *Correction:* an earlier draft inferred a DE10-Nano/SuperStation bitstream
  incompatibility from tech-press part-number specs (`5CSXFC6D6F31I7N` vs
  `5CSEBA6U23I7`). The working build's hands-on result supersedes that — treat the
  press part number as the unverified claim, not the proven flow.

---

## 1. Driving the board over SSH (the workflow backbone)

- `ssh mister` (host alias + key in `~/.ssh/config`). Root `/` is a **read-only
  loop-mounted ext4 image**; for persistent writes: `mount -o remount,rw /`, write,
  `sync`, optionally remount ro.
- **`/dev/MiSTer_cmd`** (FIFO) is the command channel — small command set:
  `load_core <path>`, `screenshot`, `Reset`, **`Mount`** (vdisks). Verify what a build
  supports: `strings /media/fat/MiSTer | grep -i ...`. **No command loads an
  arbitrary file at an index** — see §2.
- **Screenshots:** `echo screenshot > /dev/MiSTer_cmd` → PNG in
  `/media/fat/screenshots/<core>/<ts>.png`; `cat` it back. `/dev/fb0` is the raw
  framebuffer.
- **Filmstrip > single shot.** For a video core this is *the* hardware-verification
  tool: a single frame never catches playback/seek/boot behavior. Build a burst tool
  — loop `screenshot; sleep N; cp $(ls -t .../*.png|head -1) frame_NN` over a window,
  tar back, view in order. Use it to verify **play → seek → resume**, field flicker,
  and the two-library browse UI without eyeballing a TV.

## 2. Loading data INTO the core without the OSD (hands-off testing)

The OSD file picker is a manual button press; `/dev/MiSTer_cmd` can't trigger an
arbitrary load. Two automation routes:

- **`.mra` autoload (ioctl path):** a `.mra` XML names `<rbf>` + `<rom index="N">`
  parts; `load_core foo.mra` loads the rbf then streams each part to the core as an
  **`ioctl` download at `ioctl_index = N`**. Use this for **firmware/config blobs**
  (e.g. if a decoder variant needs a `firmware.mem`-style load) — map indices to what
  `emu.sv` consumes.
- **⚠️ For THIS core, prefer `Mount` (the `sd_*` path):** our decoder ingests the
  bitstream via the **`sd_*` on-demand sector seam** (`mpg_streamer.sv`), *not*
  `ioctl`. So the hands-off test loop is: build a small **vdisk image** containing a
  test `.mpg`/PS clip, `Mount` it over SSH, `load_core`, then filmstrip. This exactly
  exercises the production seam (sector requests served from an image) — the only
  difference at M2 is the HPS serves those sectors from the **network ring buffer**
  instead of the mounted image.
- This turns "play a clip on hardware" into a couple of SSH commands — the foundation
  of autonomous bring-up (M0–M1).

## 3. HPS ↔ core data paths (the seams we use)

See [`findings.md`](findings.md) §5 for the verified signal-level detail. Summary of
the two seams for this core:

- **INPUT — on-demand `sd_*` sector streaming (PRIMARY).** Core asserts `sd_rd[n]`
  + `sd_lba[n]`; HPS reads that LBA (from a mounted image *or the network ring
  buffer*) and returns it via `sd_ack`/`sd_buff_wr`/`sd_buff_dout`/`sd_buff_addr`.
  This is the disc/optical-core pattern (`cd_hps_req/lba/ack/data` on PSX). **Pull-
  paced**: you never preload the whole title, the core requests LBAs as its decoder
  drains. `MiSTer_MPEG2`'s `mpg_streamer.sv` is exactly this and is hardware-verified.
- **OUTPUT — two options (see §3.1).** The decoder produces decoded frames; getting
  them to the ADV7125 is either a **raster `VGA_*`** path or the **`FB_*` DDR
  framebuffer** path.
- **DDR3 / `DDRAM_*`** (64-bit burst) is the big off-chip buffer: frame stores +
  the decoder's circular bitstream buffer. `mem_shim.sv` already maps this onto the
  Cyclone V f2sdram bridge / CMA.
- **Disc/sector tooling:** `chdman` (from `brew install rom-tools`, **not** the giant
  `mame` formula) builds/reads CHD and `extractcd`s BIN/CUE; `chdman info` shows track
  layout. For DVD we mostly handle raw ISO/VOB, but the **on-demand-sector model is
  the same shape** — handy for building M0/M1 test vdisk images.

### 3.1 ⚠️ Video out: raster `VGA_*` vs `FB_*` framebuffer — decision

| Path | How | Fit for us |
|------|-----|-----------|
| **Raster `VGA_*`** (RECOMMENDED) | Core generates the actual pixel raster + timing (`VGA_R/G/B`, `VGA_HS/VS/DE`, `CE_PIXEL`, `CLK_VIDEO`); `sys/` routes it to HDMI **and the analog ADV7125**. | `mpeg2fpga`'s `syncgen` and the CD-i `frameplayer` already produce this. **Best for native, field-exact 480i** — you control field parity/porches into the DAC directly. |
| **`FB_*` framebuffer** | Core writes decoded frames to DDR3 and hands them to the MiSTer **scaler** (`FB_EN`, `FB_BASE`, `FB_FORMAT`, `FB_WIDTH/HEIGHT`, `FB_PAL_*`). | Easiest way to get *a* picture, and the route for HDMI/scaled output. But the scaler is oriented to scandouble/scale — **harder to guarantee field-exact native 480i**. Keep as a fallback / HDMI option. |

**Decision:** drive **`VGA_*`** with the decoder's own 480i raster for the field-exact
analog goal; treat `FB_*` as the fallback (e.g. early "just show a frame" bring-up, or
HDMI). Note `mpeg2fpga` already buffers frames in DDR internally (`frameplayer` reads
DDR line buffers) and emits a raster — so we get the best of both without MiSTer's
`FB_*`.

## 4. Building the `.rbf` (Quartus) — the slow step

- **Quartus Prime Lite 17.0.x** (Cyclone V; MiSTer-standard). Quartus is **x86-only**;
  the board's ARM can't build.
- **Docker `raetro/quartus:17.0`** (Quartus 17.0.2 Lite + Cyclone V, no Intel login):
  `docker run --rm -v "$PWD":/work -w /work --entrypoint quartus_sh raetro/quartus:17.0
  --flow compile <ProjectRevision>` → `output_files/<rev>.rbf`.
- **A dedicated cheap x86 Linux box** beats Apple-Silicon/Rosetta: builds while the Mac
  sims, frees disk. Quartus is **single-thread-bound** (Fitter barely parallelizes), so
  the win is *parallelism*, not per-build speed. ~30 min/build.
- **Make remote builds reboot-proof** — launch **detached** so a laptop sleep/reboot
  doesn't SIGHUP it: `ssh box "setsid nohup bash runner.sh > /tmp/build.log 2>&1
  </dev/null &"`. A tiny local dashboard polling the log (stage + elapsed +
  warnings/errors + `*.fit.rpt` resource usage) is a big QoL win for ~30-min builds.
- **Project wiring:** `TOP_LEVEL_ENTITY sys_top`; `source sys/sys.tcl` (sets
  FAMILY/DEVICE + pins + pulls the MiSTer `sys/` framework that instantiates our
  `emu`); `source files.qip`. The `.sof`→`.rbf` conversion is a `POST_FLOW` hook in
  `sys/` — ensure it's wired or you get a `.sof` and no `.rbf`. **⚠️ `sys.tcl`'s DEVICE
  must match the deployment board (§0).**

## 5. Simulation — see a decoded frame before you ever build a bitstream

This is the **#1 de-risk** for the gating milestone (confirmed video-out): sim the
decoder against known streams and **dump a framebuffer PNG**, gating M1 on a correct
decoded frame *in sim* before spending 30 min on a bitstream.

- **Pick the simulator by language.** `mpeg2fpga` is **Verilog**, the CD-i core is
  **SystemVerilog** → **Verilator** (or iverilog). VHDL would be **NVC** — not our
  case here. **No mature OSS tool co-simulates VHDL+Verilog in one kernel**; keep the
  language boundary clean if one ever appears.
- **Reuse `mpeg2fpga`'s own conformance bench.** Its `bench/conformance` runs the
  **MP@ML conformance bitstreams** (`make clean test`). Wire it to dump decoded frames
  → PNG, and you can validate decode correctness (incl. interlaced field output) with
  zero hardware.
- **Verilator external/observability:** tap internal signals (e.g. a decode state, an
  LBA, the `vbuf` fill) from the testbench to turn "looks like X" into "the value is
  exactly X."
- **Two hard-won cautions:**
  - **Single-writer per output/trace dir.** A background sim writing the same dir as a
    foreground run silently **clobbers** traces → false conclusions. One writer per
    dir, always.
  - **Don't claim a milestone from one run.** Boot/init is dominated by **uncached**
    memory traffic paying SDRAM-model latency (cache/"turbo" flags barely help) —
    reproduce clean before claiming. (Fits this project's "flag unverifiable claims"
    ethos: a green sim once is not a result.)

## 6. Core structure & OSD

- Top is **`emu`** (instantiated by `sys/sys_top.v`); or set `TOP_LEVEL_ENTITY sys_top`.
- OSD/options come from the **`CONF_STR`** string: `O[bit],Label,Opt0,Opt1;` →
  `status[bit]` toggle; `Fn,EXT,Label;` → file picker loading at `ioctl_index n`;
  `Pn` → submenu. `hps_io` wires it.
- Toggles persist in `/media/fat/config/<CORENAME>.CFG`. **No clean SSH way to flip an
  OSD toggle** → **design sane defaults**, drive everything testable via `Mount`/`.mra`,
  and put live catalog/playback on the **control channel** + HPS-side UI (not the OSD;
  see [`catalog-browse.md`](catalog-browse.md)).

## 7. Repo/workflow practices that paid off

- **Vendor big deps as git submodules pinned to a SHA**, keep our edits as **isolated,
  re-appliable patch files** (`git apply`) rather than committing into the submodule.
  The pin never moves, diffs stay upstream-offer-able, an idempotent `apply_patches.sh`
  (reverse-check to detect already-applied) re-applies before sim/build. **Use this for
  `core/`:** vendor `MiSTer_MPEG2` (and the MiSTer `sys/` framework, and the CD-i core
  as reference) pinned; our integration as patches.
- **Verification ladder:** unit sim → full-system sim that dumps a **framebuffer PNG**
  + trace (gate milestones) → hardware (filmstrip you actually LOOK at). The
  PNG-from-sim rung is gold for a video core.
- **One PR per coherent unit; run an adversarial review** (independent passes: logic;
  timing/synthesis-safety; protocol-vs-reference; sim-vs-hardware parity) before
  merging — it catches disqualifying bugs and over-claims single-pass review misses.
