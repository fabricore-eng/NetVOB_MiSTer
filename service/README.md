# service/ — Raspberry Pi 5 provider service

Source-agnostic MPEG-2 provider. Owns the wire to the console and emits **one
MPEG-2 Program Stream over TCP** (+ a separate JSON control channel), filled
from pluggable backends behind a common `Source` interface.

Design contracts this package implements:
[`../docs/service-design.md`](../docs/service-design.md) (Source interface,
catalog, streamer) and [`../docs/transport.md`](../docs/transport.md) (PS-over-TCP,
session preamble, buffering/pacing, seek).

## Layout

```
service/
├── core/
│   ├── catalog.py     # Catalog aggregator — keeps libraries SEPARATE/labeled (no merge, no dedupe)
│   ├── protocol.py    # Control-channel JSON schema (browse/play/pause/resume/seek/stop/status)
│   │                  #   + the one-time media-socket session preamble
│   └── streamer.py    # Drives a media socket from StreamHandle.read() with pre-buffer + pacing;
│                      #   pause/seek/stop via the handle. Pure logic (injected writer/clock).
├── sources/
│   ├── base/source.py # Source ABC + CatalogEntry / StreamHandle / NavInfo / AvInfo
│   ├── dvddump/       # DVDDumpSource — VOB PS passthrough + private_stream_2 (0xBF) nav-pack strip
│   └── plex/          # PlexSource — STUB (no creds): conforms to ABC, health() = unconfigured
├── tests/             # stdlib unittest; asserts EXACT byte/values
├── requirements.txt
└── README.md
```

## What is real vs. stubbed

| Path | Status |
|---|---|
| `Source` ABC + dataclasses, catalog separation/badging | **real, tested** |
| Control protocol round-trip + media session preamble | **real, tested** |
| Streamer pre-buffer / pacing / pause / seek / stop | **real, tested** |
| DVDDumpSource **PS passthrough + 0xBF nav-pack stripping** | **real, tested** (lossless, byte-for-byte) |
| DVDDumpSource `browse()` (IFO titles + main-feature flag) | **real, tested** — pure-Python VMGI/VTSI parse; `TODO(metadata)` disc-ID naming |
| DVDDumpSource `open(VTS_nn_x)` **ordered PGC cell streaming** | **real, tested** — VTS IFO → PGC program map + cell playback table (`C_PBKIT`) → ordered `(vob_file, byte-range)` spans, nav-stripped; cell-granular `NavInfo` seek index. `TODO(time-map)` DSI/VOBU index |
| PlexSource (catalog + ffmpeg transcode) | **stub** — `browse()`=`[]`, `open()` raises; no `PLEX_URL`/`PLEX_TOKEN` |

The DVD nav-strip is the verifiable-now spine: a VOB *is* an MPEG-2 Program
Stream, so `open()` strips the DVD `private_stream_2` (`stream_id 0xBF`,
PCI/DSI nav) PES packets and passes **every other pack/PES through losslessly**.
For a real dump, `open("dvddump:VTS_nn_x")` parses the VTS IFO's PGC (program
map + cell playback table) and streams the title's cells **in PGC playback
order**, mapping each cell's VTS-relative sector range onto the on-disk
`VTS_nn_1.VOB` .. set (splitting cells that straddle the 1 GB VOB-file boundary)
— the lossless, field-exact PS the wire needs. The cell starts double as a
coarse `NavInfo` seek index; a finer DSI/VOBU time-map index is a `TODO`.

## Run / test

No third-party packages are required — the core uses the **Python 3.14 standard
library only**, so the tests run on a bare interpreter:

```sh
# from the repo root
python3 -m unittest discover -s service/tests -v
```

`requirements.txt` is intentionally empty of runtime deps for now (it documents
the deps that future stubbed paths — `pydvdid`, `PlexAPI`, plus system `ffmpeg`
/ `libdvdread` — will pull in).

## Deployment note

`core/` (protocol + streamer) is pure logic with no socket/Plex/DVD imports, so
the *same* code runs on the Pi for network sources and on the console ARM for
future local sources (`is_local_to_console = True`) — a deployment detail, not
an interface change.
