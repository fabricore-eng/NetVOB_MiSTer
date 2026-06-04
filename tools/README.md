# tools/ — test harness & utilities

Supporting utilities that de-risk the milestones. Not part of the runtime path. See
[`../docs/dev-workflow.md`](../docs/dev-workflow.md).

The **scripts** here are the durable artifacts; their **output is gitignored**
(`tools/**/out/`, `tools/**/*.png|*.mpg|*.vob|*.m2v`) — regenerate it, never commit it.

## Built (runnable, locally verified)

### `clips/make_clips.sh` — canned MPEG-2 PROGRAM STREAM test clips

Synthesizes two known-good NTSC-raster (720x480) MPEG-2 **program streams** (the exact
payload the FPGA decoder ingests — DVD `.VOB` == MPEG-2 PS) into `clips/out/`:

- `test480i.mpg` — **interlaced** (`-flags +ilme+ildct -top 1 -field_order tt`),
  moving `testsrc2` so the two fields differ; 29.97 fps frames = 480i59.94.
- `test480p.mpg` — **progressive** `smptehdbars`; 59.94p.

Idempotent and parameterized by duration. After generating, it self-verifies each clip
with `ffprobe` (codec == `mpeg2video`, container == MPEG program stream, and for 480i
that `field_order` + per-frame `interlaced_frame=1` are present) and prints a summary.

```sh
tools/clips/make_clips.sh            # default 2s
tools/clips/make_clips.sh 4          # 4s
DURATION=1 tools/clips/make_clips.sh # via env
```

### `filmstrip/filmstrip.py` — contact-sheet stitcher (the software "filmstrip" rung)

Samples N evenly-spaced frames and stitches them into one grid PNG for a quick visual
scan — the software analogue of dev-workflow.md's hardware filmstrip rung (a single
frame never catches playback/seek/field behavior). **ffmpeg + Python stdlib only**
(no PIL/pip; the PNG writer is built in).

Two input modes:
- a **video clip** → frames sampled at N timestamps across its duration;
- a **directory of PNGs** (Verilator sim dumps, or screenshots pulled back from
  hardware) → N picked evenly across the sorted set.

```sh
# from a clip, 8 frames, with burned-in timestamp labels
tools/filmstrip/filmstrip.py tools/clips/out/test480i.mpg -n 8 --label

# from a frame-dump directory
tools/filmstrip/filmstrip.py path/to/frames/ -n 12 --cols 4 -o sheet.png
```

Output defaults to `filmstrip/out/<name>.filmstrip.png` (or `<dir>/filmstrip.png`).
Flags: `-n/--frames`, `-o/--output`, `--cols`, `--cell-width`, `--gap`, `--label`.

> Note: video sampling uses ffmpeg **output seeking** (`-ss` after `-i`) because raw
> MPEG program streams carry no seek index — input seeking overshoots/fails near EOF.
> Decode-then-seek is reliable (and fast enough) for short test clips.

## Planned (design-only for now)

Planned:
- **Canned MPEG-2 PS test streams** — known-good 480i clips (+ a progressive 480p
  variant for early bring-up) for M1–M2 before real sources exist. Generate with
  `ffmpeg -c:v mpeg2video … -f vob`.
- **Verilator conformance → PNG harness** — drive `mpeg2fpga`'s `bench/conformance`
  (MP@ML bitstreams) under Verilator and **dump decoded frames as PNG**. The "see a
  decoded frame before a bitstream" rung of the verification ladder (M0/M1).
- **Test-vdisk / CHD builder** — wrap a test `.mpg`/PS clip into a vdisk image (and/or
  CHD via `chdman` from `brew install rom-tools`) so it can be **`Mount`ed** over SSH to
  exercise the `sd_*` sector path on hardware (M1b). `chdman info` for track layout.
- **`Mount`/`.mra` autoload helper** — scripts to `load_core` + `Mount` a test image
  (sd_* path, our default) or build a `.mra` for an `ioctl_index` blob load if a
  decoder variant needs firmware/config.
- **Filmstrip tool** — burst `screenshot > /dev/MiSTer_cmd` over a window, pull the
  PNGs back, view in order. The hardware-verification workhorse for play/seek/browse.
- **Stub Pi server** — streams a canned PS file over TCP + minimal control channel,
  for M2 before the full service/plugins exist.
- **disc-id utility** — DVD fingerprint (dvdid-style CRC over `VIDEO_TS.IFO` +
  `VTS_01_0.IFO` + title sizes/durations) → title lookup (DVD-ID → TMDB), with
  sidecar/folder-name fallbacks. Powers DVDDumpSource metadata (M4); runnable standalone.
- **field-cadence verification helpers** — check field parity / 3:2 pulldown against a
  capture (M7).
