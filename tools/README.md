# tools/ — test harness & utilities

Supporting utilities that de-risk the milestones. Not part of the runtime path. See
[`../docs/dev-workflow.md`](../docs/dev-workflow.md).

> **No code yet — planning phase.**

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
