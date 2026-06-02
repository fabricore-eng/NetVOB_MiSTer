# arm/ — console HPS (ARM Linux) ingest app

Receives the **MPEG-2 Program Stream over TCP**, unwraps it, and feeds the FPGA
decoder via the **`sd_*` CD-sector seam** — plus audio decode and A/V sync.

> **No code yet — planning phase.** Transport/sync design:
> [`../docs/transport.md`](../docs/transport.md); seam: [`../docs/findings.md`](../docs/findings.md) §5.

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
