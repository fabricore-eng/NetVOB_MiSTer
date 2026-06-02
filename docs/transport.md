# Transport — wire protocol, buffering, flow-control, A/V sync

## 1. The container layers (plain version)

| Layer | What it is | Used for | In this project |
|---|---|---|---|
| **ES** | bare compressed video (or audio) bits | the decoder's actual input | `mpeg2fpga` ingests video **ES**; ARM produces it |
| **PES** | ES + **PTS/DTS** timestamps | A/V sync; shared by PS *and* TS | the sync layer we rely on for lip-sync |
| **PS** (Program Stream) | PES + pack headers w/ **SCR** | storage on reliable media | **a DVD `.VOB` IS this**; our **wire format** |
| **TS** (Transport Stream) | PES chopped into 188-byte packets + **PCR** | broadcast/streaming over **lossy** channels | the fallback (see §3) |

The FPGA decoders want **ES** (`mpeg2fpga`) or **PS** (CD-i's in-fabric demuxer) —
**never TS**. So whatever we put on the wire, the **ARM unwraps it to ES** before the
`sd_*` seam regardless.

## 2. Decision: **MPEG-2 Program Stream over TCP** (single media protocol) + separate control channel

**Why PS over TCP:**

1. **Lossless DVD spine.** A VOB is already a PS; DVDDumpSource just streams its
   bytes. No PS→TS→ES double conversion. Maximum field/lip fidelity, near-zero CPU.
2. **TCP removes TS's reason to exist.** TS's packetization/PCR machinery exists to
   survive *lossy* links (UDP/RF). On a **wired GbE LAN over TCP**, delivery is
   reliable and in-order, so PS's lack of loss-recovery doesn't bite.
3. **A/V sync comes for free.** PS already carries audio+video multiplexed with
   PTS/DTS — exactly what we need for lip-sync. The ARM reads the PTS it's already
   given.
4. **ARM as universal adapter.** ARM demuxes PS → video ES (→ `sd_*` seam) and
   PS → audio ES (→ AC-3/MP2 decode → PCM → I2S), see §5.
5. **PlexSource is just as easy** — `ffmpeg … -f vob` emits PS.

**Control channel** (separate TCP or WebSocket, JSON): `browse`, `play{id}`,
`pause`, `resume`, `seek{t}`, `stop`, `status`. Decoupling control from media keeps
seek/pause responsive and lets the UI query catalogs without disturbing the stream.

**Framing:** the media socket is a **raw PS byte stream** (no custom per-packet
framing needed — PS is self-delimiting via pack/PES start codes). A tiny session
preamble (stream id, `AvInfo`, duration, nav-index availability) is sent once at
`open`. Avoiding custom framing keeps us compatible with off-the-shelf PS tooling
for debugging (you can pipe the socket into `ffplay`/`mpv`).

## 3. The TS fallback — when to switch

Keep a clean PS↔TS boundary so this is a config flip, not a rewrite. Switch to
**TS** (or PS-over-a-loss-tolerant transport) if any of these become real:

- You move off wired GbE to **Wi-Fi/UDP/multicast** where you can't buffer through
  loss (the SuperStation has Wi-Fi; quality guidance is "use wired").
- You want to reuse **Plex's native HLS** (TS segments) without re-muxing.
- You want one stream fanned out to **multiple consoles** (TS multiprogram).

None apply to the primary, single-console, wired scope — so PS-over-TCP wins now.

> **Note:** this diverges from the original "always emit TS" wording. The change is
> deliberate and localized (DVD VOB is already PS; TCP gives reliability). If you
> prefer to force TS regardless, it's a one-line change here + a TS demux step on the
> ARM.

## 4. Buffering & flow-control (where each piece runs)

Three buffers, each with a clear owner; the **decoder's pull is the ultimate
pacer.**

```
Pi: source → [pre-buffer ~1–3 s] → TCP ──► ARM: [ring buffer, seconds] ──(sd_* pull)──► FPGA: [input FIFO]
        (smooths transcode/seek)        (TCP flow ctrl)   (decoder pulls as it drains)   (mpeg2fpga vbuf / 32k-class FIFO)
```

- **Pi pre-buffer (seconds):** absorbs PlexSource transcode jitter and seek
  restarts. For DVDDumpSource it's tiny (disk read is fast).
- **TCP flow control (automatic):** if the ARM stops reading, TCP's window stalls the
  Pi — natural backpressure, no custom protocol.
- **ARM ring buffer (seconds):** the heart of pacing. The ARM **only `recv()`s as
  much as it needs to keep the ring non-empty**, and the FPGA **only pulls sectors
  (`sd_rd`) as its input FIFO drains**. So: FPGA FIFO low-water → ARM serves a sector
  from the ring → ring low-water → ARM reads more from TCP → TCP backpressures the
  Pi. End-to-end pull, paced by the decoder's real consumption.
- **FPGA input FIFO:** `mpeg2fpga`'s `vbuf`/`getbits` front-end (the CD-i analog is
  the 32k dual-clock FIFO). Plus, the `sd_*` path has **`ioctl_wait`-style
  backpressure** available if needed.

**Bandwidth reality:** DVD-rate MPEG-2 ≈ 6–15 Mbps ≈ <2 MB/s — trivial for TCP/GbE
and below even the SPI-limited `sd_*` path (tens of MB/s). Buffering is about
**latency/jitter/seek**, not throughput.

## 5. A/V sync strategy (audio decoded on the ARM — decision D2)

**Master clock = the video display.** The decoder is **VSYNC-paced** (this is how
`MiSTer_MPEG2` already behaves), i.e. the FPGA presents fields/frames locked to the
console's 480i output timing. Audio is **slaved to video.**

Pipeline:
1. ARM demuxes PS → video PES (with PTS) → video ES → `sd_*` seam; and PS → audio PES
   (with PTS) → AC-3/MP2 **decode on the ARM** → PCM.
2. ARM tracks the **video presentation clock** (derived from the FPGA's actual
   field/frame cadence — e.g. a vblank tick the core exposes, or the known 480i field
   rate) and compares audio PTS against it.
3. Audio is kept in lip-sync by **slewing**: small **resample** adjustments (drop the
   buffer ± a few ppm) and, for large drift (after seek/underrun), a one-time
   **PTS-based resync** (skip/insert audio to match the current video PTS). Audio
   goes out the MiSTer I2S/analog path.
4. Because PS keeps audio+video on a common SCR/PTS base from the source, steady-state
   drift is tiny; the ARM mostly just maintains a fixed audio delay equal to the
   video pipeline latency.

**Open design choices (resolve in M6):**
- Exact video clock reference the ARM uses (a core-exposed vblank/frame counter is
  cleanest — small RTL addition).
- Whether the ARM owns a fixed end-to-end **A/V latency budget** (decode + FIFO +
  display) and simply delays audio by it.
- Handling **3:2 pulldown / film cadence** for true field-exact output (M7) — the
  decoder must emit fields in the correct cadence; audio sync keys off the same
  presentation clock so it follows automatically.

**If/when audio is deferred** (it isn't, per D2): sync collapses to "video paces
itself to the display," trivial.

## 6. Seek across GOP boundaries

MPEG-2 is GOP-structured; you can only cleanly **start decoding at an I-frame**.
Strategy, split by where work happens:

1. **UI** sends `seek{t}` on the control channel.
2. **Pi (source) finds the nearest preceding GOP/I-frame**:
   - **DVDDumpSource:** use the IFO/**VOBU** navigation (DSI packets carry seek
     points / time map) to jump to the VOBU/GOP at-or-before `t` — exact and cheap.
     `StreamHandle.nav` exposes this index.
   - **PlexSource:** `ffmpeg -ss` to the nearest keyframe before `t` (re-launch the
     transcode from there); accept a small snap-to-GOP.
3. **Flush** the in-flight buffers (Pi pre-buffer, ARM ring, FPGA input FIFO) and
   signal the decoder to **reset to a clean I-frame** so no stale references render.
4. **Resume** streaming PS from the new point; the ARM re-establishes A/V sync from
   the first post-seek PTS (§5 large-drift resync path).

**Field-cadence note:** seeking must land on a point that preserves field parity
(top/bottom field order) so 480i output stays field-exact; the VOBU/GOP boundary is
a safe landing for this. Tracked under M7.

## 7. Summary of who runs what

| Piece | Pi 5 | Console ARM | FPGA |
|---|---|---|---|
| Mux/emit PS, PTS intact | ✅ | | |
| Pre-buffer + seek (find GOP/VOBU) | ✅ | | |
| TCP media + control | ✅ (server) | ✅ (client) | |
| PS demux → video ES / audio ES | | ✅ | |
| Feed video ES via `sd_*` seam | | ✅ | (consumes) |
| Audio decode → PCM → I2S | | ✅ | |
| A/V sync (slave audio to video) | | ✅ | (vblank ref) |
| Video decode → vblank-latched RGB | | | ✅ |
