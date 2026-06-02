# tools/ — test harness & utilities

Supporting utilities that de-risk the milestones. Not part of the runtime path.

> **No code yet — planning phase.**

Planned:
- **Canned MPEG-2 PS test streams** — known-good 480i clips (and a progressive 480p
  variant for early bring-up) used in M1–M2 before real sources exist. Generate with
  `ffmpeg -c:v mpeg2video … -f vob`.
- **Stub Pi server** — streams a canned PS file over TCP + minimal control channel,
  for M2 before the full service/plugins exist.
- **disc-id utility** — compute a DVD fingerprint (dvdid-style CRC over
  `VIDEO_TS.IFO` + `VTS_01_0.IFO` + title sizes/durations) and resolve to a title
  (DVD-ID lookup → TMDB), with sidecar/folder-name fallbacks. Powers DVDDumpSource
  metadata (M4); kept here so it can be run/tested standalone.
- **field-cadence verification helpers** — scripts to check field parity / 3:2
  pulldown against a capture (M7).
