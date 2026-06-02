# Service design — Pi 5 provider + source-plugin interface

The provider service runs on the Raspberry Pi 5, **alongside but independent of
Plex**. It owns the wire to the FPGA core and always emits **one clean MPEG-2
Program Stream over TCP** (see [`transport.md`](transport.md)), filled from pluggable
backends behind a common `Source` interface. The console speaks only the one
protocol; **each backend's quirks stay quarantined inside its plugin.**

```
        ┌───────────────────────── provider service (Pi 5) ─────────────────────────┐
        │  sources/ (plugins)                core/                                    │
        │  ┌──────────────┐                 ┌───────────────────────────────────┐    │
        │  │ DVDDumpSource │──┐             │ Catalog aggregator                 │    │
        │  ├──────────────┤  │  Source ABC  │  (keeps libraries SEPARATE)        │    │
        │  │ PlexSource    │──┼────────────►│                                    │    │
        │  ├──────────────┤  │             │ Session manager + Streamer          │    │
        │  │ (future)…     │──┘             │  • open(id) → PS byte iterator      │    │
        │  └──────────────┘                │  • pre-buffer, pacing, control      │    │
        │                                  └──────────────┬────────────────────┬─┘    │
        └─────────────────────────────────────────────────┼────────────────────┼──────┘
                                          media: PS/TCP ───┘    control: TCP/WS ─┘
```

---

## 1. The `Source` interface (the plugin boundary)

A backend implements a small, stable interface. **Two methods are the contract**
(`browse`, `open`); the rest is metadata/lifecycle. Designed so a future
console-local backend (DiscSource, SSDDumpSource) is "add a class," never a
refactor.

```python
# service/sources/base/source.py   (illustrative — NOT an implementation deliverable)

class CatalogEntry:
    id: str            # opaque, source-scoped (e.g. "dvddump:matrix-1999")
    title: str
    kind: str          # "movie" | "episode" | "title" | ...
    duration_s: float | None
    poster_url: str | None      # Plex has these; dumps usually don't
    badge: str         # "field-exact" (dump) | "transcoded" (Plex) — see catalog-browse.md
    extra: dict        # source-specific (season/ep, disc title #, chapters…)

class StreamHandle:
    # A pull iterator of MPEG-2 Program Stream bytes + the metadata the
    # streamer/ARM need for sync and seek.
    def read(self, n: int) -> bytes: ...        # next PS bytes (blocking/back-pressured)
    def seek(self, t_seconds: float) -> None:   # seek to nearest preceding I-frame/GOP
    duration_s: float | None
    nav: "NavInfo | None"     # GOP/VOBU index if available (DVD), else None
    av: "AvInfo"              # video: mpeg2; audio: ac3|mp2|none; pts base; field cadence hint
    def close(self) -> None: ...

class Source(ABC):
    name: str                 # stable library name, e.g. "DVD Dumps", "Plex"
    badge: str                # default badge applied to entries
    is_local_to_console: bool # False for network plugins; True for future Disc/SSD
    def browse(self, path: str | None = None) -> list[CatalogEntry]: ...
    def open(self, id: str) -> StreamHandle: ...
    def health(self) -> dict: ...   # reachable? auth ok? for the UI
```

**Key property:** `open(id)` returns **MPEG-2 Program Stream bytes** regardless of
backend. Whatever a backend has to do to get there (DVD demux, Plex transcode) is
its own business. The core never imports a Plex or DVD type.

### Why PS (not TS/ES) at the plugin boundary
- DVD VOB **is already PS** → DVDDumpSource is a passthrough (near-zero CPU).
- PS carries audio+video+PTS together, so the streamer/ARM get sync "for free."
- The ARM demuxes PS→video-ES for the decoder and PS→audio-ES for ARM audio decode.
- Full rationale + the TS fallback: [`transport.md`](transport.md).

---

## 2. Service core

Stateless-per-request where possible; one active **session** per connected console.

- **Catalog aggregator** — calls each enabled source's `browse()`. **Keeps results
  in separate, labeled lists** (`{"DVD Dumps": [...], "Plex": [...]}`). No merge, no
  dedupe, ever. Caches listings; refresh on demand or TTL.
- **Session manager** — on a `play` control message, calls the right source's
  `open(id)`, gets a `StreamHandle`, and hands it to the Streamer.
- **Streamer** — drives the media TCP socket from `StreamHandle.read()`, with a
  small pre-buffer and pacing (the ARM/decoder ultimately pace via pull/backpressure;
  see transport). Handles `pause`/`seek`/`stop` by manipulating the handle.
- **Control channel** — a separate TCP/WebSocket carrying JSON:
  `browse`, `play{id}`, `pause`, `resume`, `seek{t}`, `stop`, `status`. Decoupling
  control from media keeps seek/pause snappy and lets the UI query catalogs without
  touching the media socket.

**Language:** Python. Orchestration, Plex API, and control are I/O-bound; the
performance-critical DVD remux is **byte copying**, not CPU-bound; `ffmpeg` runs as
a subprocess for Plex transcode. (If the DVD demux ever needs to be faster, the hot
loop can drop to a small C extension — but PS passthrough is already trivial.)

---

## 3. The two network plugins (primary scope)

### 3.1 DVDDumpSource — the lossless spine

- **Input:** your VOB/VIDEO_TS dumps (a `VIDEO_TS/` folder with `VTS_xx_y.VOB`,
  `VIDEO_TS.IFO`, `VTS_xx_0.IFO`).
- **`browse()`:** parse the IFO files (e.g. `libdvdread`) to enumerate **titles**
  (title sets, angles, durations, chapter/PGC structure). No transcode. Title naming
  comes from the metadata layer (disc-ID lookup; see
  [`catalog-browse.md`](catalog-browse.md)) since IFOs carry no human titles.
- **`open(id)`:** **pass the program stream through.** A VOB *is* an MPEG-2 PS, so
  the work is: follow the IFO/PGC/VOBU navigation to read the selected title's VOB
  cells in order, **strip DVD nav packets** (the private-stream-2 PCI/DSI packets) so
  the wire carries a clean PS, and stream the bytes. Lossless, field-exact, near-zero
  CPU. Expose the **VOBU/GOP index** in `StreamHandle.nav` for clean seeking.
- **Quarantined quirks:** IFO parsing, PGC/cell ordering, multi-angle, nav-pack
  stripping, region/CSS (assume already decrypted dumps). None of this leaks past
  `open()`.
- **Library label / badge:** **"DVD Dumps" / `field-exact`.**

### 3.2 PlexSource — the transcoded second library

- **Input:** a Plex Media Server reachable on the LAN; user-provided token.
- **`browse()`:** query the Plex API for libraries/sections/items; map to
  `CatalogEntry` with **posters, titles, season/episode** (Plex's rich metadata is a
  feature here). 
- **`open(id)`:** **pull the original media and transcode to 480i MPEG-2 PS on the
  Pi** with `ffmpeg` (`mpeg2video` + audio `ac3`/`mp2`, `-f vob`/`-f dvd` →
  Program Stream). *Plex does not natively transcode to MPEG-2*, so we do not lean on
  the PMS transcoder for the codec; we use Plex as a **catalog + file source** and
  own the encode. (Reading the original file may use the Plex "download/parts"
  endpoint or a direct path if the library is local to the Pi.)
- **Quarantined quirks:** Plex auth/token refresh, the (partly private) API surface,
  HLS/transcode-decision endpoints if ever used, path mapping. All inside the plugin.
- **Encode budget:** one 480i MPEG-2 stream is comfortable on the A76 **alone**;
  **flag contention** if Plex is simultaneously transcoding for another client (no HW
  encoder to offload to). Active cooling assumed. Encoder settings (bitrate, GOP,
  field order, 3:2 handling) live in [`transport.md`](transport.md) / M5 + M7.
- **Library label / badge:** **"Plex" / `transcoded`.**

---

## 4. Future-tier backends (design-for, do NOT build)

The interface already admits these — they're just `Source` implementations with
`is_local_to_console = True` that happen to run on the **console ARM** instead of the
Pi. Nothing about them gates current work.

- **DiscSource** — live DVD from a SuperDock DVD-RW: same PS-passthrough logic as
  DVDDumpSource, but reading the optical drive locally on the ARM.
- **SSDDumpSource** — VOB dumps on the SuperDock NVMe: same as DVDDumpSource,
  different storage root, on the ARM.
- **Out of scope entirely:** SuperDock "PC Mode."
- **Parked:** whether the SuperStation firmware exposes the optical drive as a
  generic OS block device (PS1/Saturn disc loading is per-system firmware work).
  Tracked as a **future** risk; does not block anything now.

**Deployment note:** because a local backend runs on the ARM, the "service" is
written so its `core/` (protocol + streamer) can also run on the console for local
sources — i.e. the *same* `Source` interface and streamer, just co-located. For the
two network plugins it runs on the Pi. This is a deployment detail, not an interface
change, which is the whole point of putting `is_local_to_console` on the interface.

---

## 5. How isolation is enforced (summary)

| Concern | Where it lives | Never seen by |
|---|---|---|
| DVD IFO/PGC/VOBU nav, nav-pack stripping | `sources/dvddump/` | core, ARM, Plex plugin |
| Plex auth, API quirks, transcode params | `sources/plex/` | core, ARM, DVD plugin |
| Owning the wire, pacing, control protocol | `core/` | the plugins |
| PS→ES demux, audio decode, A/V sync, `sd_*` feed | ARM ingest app | the Pi entirely (it just gets PS) |

The contract between every layer is **"MPEG-2 Program Stream + a little metadata."**
That single seam is why a new backend never forces a rearchitecture.
