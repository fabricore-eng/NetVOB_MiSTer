# service/ — Raspberry Pi 5 provider service

Source-agnostic MPEG-2 provider. Owns the wire to the console and emits **one MPEG-2
Program Stream over TCP** (+ a control channel), filled from pluggable backends
behind a common `Source` interface.

> **No code yet — planning phase.** Design: [`../docs/service-design.md`](../docs/service-design.md).

Planned layout:

```
service/
├── core/                # PS-over-TCP server, pre-buffer/pacing, control channel, catalog aggregator
└── sources/             # pluggable backends (the plugin boundary)
    ├── base/            #   Source ABC (browse/open), CatalogEntry, StreamHandle
    ├── dvddump/         #   DVDDumpSource — VOB/VIDEO_TS → nav-stripped PS passthrough (the spine)
    └── plex/            #   PlexSource — Plex API + ffmpeg → 480i MPEG-2 PS (transcoded)
    #   future (design-only): disc/ (SuperDock DVD-RW), ssddump/ (NVMe) — run on the console ARM
```

Language: **Python** (I/O-bound orchestration; `ffmpeg` subprocess; `libdvdread`/
`dvdnav` for DVD nav; DVD remux is byte-copy, not CPU-bound). Two libraries are kept
**separate** — never merged or deduped.
