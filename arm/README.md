# arm/ — console HPS (ARM Linux) ingest app

Receives the **MPEG-2 Program Stream over TCP**, unwraps it, and feeds the FPGA
decoder via the **`sd_*` CD-sector seam** — plus audio decode and A/V sync.

> Transport/sync design: [`../docs/transport.md`](../docs/transport.md); seam:
> [`../docs/findings.md`](../docs/findings.md) §5.

## Host-testable core (built, no board needed)

`ps_demux.{h,c}` is a **streaming MPEG-2 Program Stream demuxer** — the first
buildable piece of the ingest app. It is off-target only: plain C11, **no
FPGA/MiSTer headers**, compiles with `cc`/`clang` on a dev workstation.

- Feed it PS bytes in arbitrary chunks (suited to a ring-buffer / `recv()` feed).
- It splits PS → **video ES** (the bytes bound for the `sd_*` seam), emitted via
  a sink callback; and recognizes **audio PES** from the start — MPEG audio
  (`0xC0`–`0xDF`) and AC-3/DTS/LPCM via `private_stream_1` (`0xBD`) — counting
  and routing them (decode lands later, per decision D2).
- It extracts **PTS/DTS** from PES headers (full 33-bit) for the A/V-sync
  bookkeeping in [`../docs/transport.md`](../docs/transport.md) §5.
- It skips pack headers (`0xBA`), system headers (`0xBB`), program-stream maps
  (`0xBC`), **padding** (`0xBE`) and **nav `private_stream_2`** (`0xBF`).

Build/run the unit test:

```sh
make -C arm test        # cc -std=c11 -Wall -Wextra; runs tests/test_ps_demux.c
```

The test crafts a PS buffer in memory and asserts exact video-ES output, exact
PTS/DTS decode, audio detection/counting, nav+padding drop, and that the parser
is feed-chunking invariant (one-shot == byte-by-byte) on both bounded and
length-0 (unbounded) video PES.

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
