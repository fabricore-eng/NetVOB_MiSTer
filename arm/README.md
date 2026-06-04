# arm/ — console HPS (ARM Linux) ingest app

Receives the **MPEG-2 Program Stream over TCP**, unwraps it, and feeds the FPGA
decoder via the **`sd_*` CD-sector seam** — plus audio decode and A/V sync.

> Transport/sync design: [`../docs/transport.md`](../docs/transport.md); seam:
> [`../docs/findings.md`](../docs/findings.md) §5.

## Host-testable core (built, no board needed)

All off-target only: plain C11, **no FPGA/MiSTer headers**, compiles with
`cc`/`clang` on a dev workstation. Three components model the §4 ingest seam
end-to-end without hardware:

### `ps_demux.{h,c}` — streaming MPEG-2 Program Stream demuxer

- Feed it PS bytes in arbitrary chunks (suited to a ring-buffer / `recv()` feed).
- It splits PS → **video ES** (the bytes bound for the `sd_*` seam), emitted via
  `ps_video_es_sink`; and **audio ES** — MPEG audio (`0xC0`–`0xDF`) and
  AC-3/DTS/LPCM via `private_stream_1` (`0xBD`) — emitted via the optional
  `ps_audio_es_sink` (tagged with the originating `stream_id` so the future
  AC-3/MP2→PCM stage can fan out by track, per decision D2). With no audio sink
  installed, audio is still recognized/counted exactly as before.
- It extracts **PTS/DTS** from PES headers (full 33-bit) for the A/V-sync
  bookkeeping in [`../docs/transport.md`](../docs/transport.md) §5.
- It skips pack headers (`0xBA`), system headers (`0xBB`), program-stream maps
  (`0xBC`), **padding** (`0xBE`) and **nav `private_stream_2`** (`0xBF`).
- Install the audio sink with `ps_demux_set_audio_sink()`; **video ES routing is
  byte-identical** whether or not an audio sink is present.

### `ringbuf.{h,c}` — SPSC byte ring (the §4 ARM ring)

The buffer between TCP `recv()` (producer) and the `sd_*` sector pull (consumer).
Fixed capacity, **low/high water marks**, and explicit **full / empty / overrun
/ underrun** semantics: `ringbuf_put` is all-or-nothing (rejects an overrun,
never clobbers unread data — modelling TCP backpressure), `ringbuf_get_exact`
is all-or-nothing (rejects an underrun), `ringbuf_get` is best-effort and reports
short reads. Water-mark queries (`ringbuf_below_low_water` / `ringbuf_at_high_water`)
drive the pull-pacing decisions.

### `feeder.{h,c}` — model of the `sd_*` sector-pull backpressure

A host-testable model of the end-to-end **pull pacing**: a modeled FPGA decoder
input FIFO drains (caller-driven, modelling the decoder consuming bits), and the
feeder pulls a **fixed-size sector** of video ES from the ring **only while the
FIFO is at/below its low-water mark** and a whole sector is available — so it
pulls exactly as the FIFO drains, **never overruns** the FIFO, and conserves every
byte. The `sd_*` seam is abstracted as a plain `sector_sink` callback (on hardware
that becomes the `sd_buff_dout`/`sd_buff_wr` write), so there are no MiSTer headers.

Build/run all unit tests:

```sh
make -C arm test    # cc -std=c11 -Wall -Wextra -Werror; builds + runs all tests
```

Tests crafted in-memory (no files, no FPGA) assert EXACT behaviour:
- **demux:** video-ES output, PTS/DTS decode, audio detection/counting, nav+padding
  drop, feed-chunking invariance (one-shot == byte-by-byte) on bounded and
  length-0 (unbounded) video PES;
- **audio routing:** audio ES bytes == concatenated audio PES payloads (per
  `stream_id` class), while video ES stays byte-identical to the no-audio-sink case;
- **ringbuf:** fill-to-capacity, overrun rejection, underrun, head/tail wrap
  byte-exactness, water-mark transitions;
- **feeder:** pull-only-as-FIFO-drains (no eager fill), no FIFO overrun, total-bytes
  conservation across the chain, starved-ring backpressure (no partial sector).

All four binaries also pass clean under `-fsanitize=address,undefined`.

Responsibilities:
- **TCP media client** + **control-channel client** (browse/play/pause/seek/stop).
- **Ring buffer** (seconds) — paced by the decoder's pull and TCP backpressure.
- **PS demux** → video **ES** (→ `sd_*` seam: `sd_lba`/`sd_rd`/`sd_ack`/
  `sd_buff_*`) and audio **ES** (→ decode).
- **Audio decode (AC-3/MP2) → PCM → MiSTer I2S/analog**, **lip-synced** to the
  video presentation clock via PTS (decision D2).
- **A/V sync** (slave audio to video; resample-slew + post-seek PTS resync) and
  **GOP/VOBU-aligned seek** (flush buffers + decoder I-frame reset).

Modeled on `Main_MiSTer`'s sector-service code (`user_io.cpp` `cdi_read_cd`/
`psx_read_cd` handlers) — swap the local-image read for a read from the network ring
buffer. Likely C/C++ (or a userspace daemon cooperating with `Main`).
