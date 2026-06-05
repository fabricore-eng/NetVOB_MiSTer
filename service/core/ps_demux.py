"""MPEG-2 Program Stream -> video Elementary Stream demux (reference implementation).

Per ``transport.md`` (§2, §28, §93) the wire format is an MPEG-2 **Program Stream**
(a DVD ``.VOB`` *is* a PS), and the **ARM ingest app demuxes PS -> video ES** before
feeding the ``sd_*`` seam — because the ``mpeg2fpga`` VLD front-end (``probe.v`` /
``getbits.v`` / ``vld.v``) consumes a *video elementary stream*, not a PS: its
start-code FSM only handles video codes (sequence ``0xB3`` / GOP ``0xB8`` / picture /
slice / extensions), NOT pack (``0xBA``) / system (``0xBB``) / PES (``0xE0``) codes.

This module is the **reference demux** (and a test/fixture tool): it strips the PS
pack/system/PES layering and concatenates the chosen video stream's PES payloads into
a contiguous video ES. The production demux is C on the ARM (streaming, sector-by-
sector); this Python version validates the logic and converts a real DVD PS into an ES
clip that our Verilator harness (and the HW ``sd_*`` feed) can consume directly.

It does NOT touch the stream path for the *passthrough* model (M3 streams PS as-is);
it's the M2 layer that turns PS into what the decoder actually eats.

References: ISO/IEC 13818-1 (MPEG-2 Systems), §2.5 (pack), §2.4.3.6 (PES packet).
"""

from __future__ import annotations

from typing import Optional

# Start codes (the 4th byte after the 0x000001 prefix).
PACK_START = 0xBA            # pack_header
SYSTEM_HEADER = 0xBB         # system_header
PROGRAM_END = 0xB9           # MPEG_program_end_code
# Stream ids whose PES packet body is RAW (no PES header to skip).
_RAW_BODY_STREAMS = frozenset({
    0xBC,  # program_stream_map
    0xBE,  # padding_stream
    0xBF,  # private_stream_2 (DVD nav packs live here)
    0xF0, 0xF1, 0xF2,  # ECM / EMM / DSMCC
    0xF8,  # ITU-T Rec. H.222.1 type E
    0xFF,  # program_stream_directory
})

# Default video stream id: video stream number 0 == 0xE0 (DVD main video).
VIDEO_STREAM_0 = 0xE0


def demux_ps_to_video_es(data: bytes, stream_id: int = VIDEO_STREAM_0) -> bytes:
    """Extract the video elementary stream for ``stream_id`` from an MPEG-2 PS buffer.

    Walks pack -> system_header -> PES packets, concatenating the PES *payloads* of the
    requested video stream (skipping each PES header). Returns the contiguous video ES.

    Robust to leading/trailing garbage and to a stream that doesn't start on a pack: it
    resyncs by scanning for the next ``00 00 01`` start code whenever framing is lost.
    """
    out = bytearray()
    n = len(data)
    i = 0
    while i + 4 <= n:
        # Require a start-code prefix; if absent, resync to the next one.
        if not (data[i] == 0x00 and data[i + 1] == 0x00 and data[i + 2] == 0x01):
            nxt = data.find(b"\x00\x00\x01", i + 1)
            if nxt < 0:
                break
            i = nxt
            continue
        code = data[i + 3]

        if code == PROGRAM_END:
            break

        if code == PACK_START:
            # MPEG-2 pack_header: 0x000001BA + 10 fixed bytes; low 3 bits of byte 13
            # are pack_stuffing_length. (MPEG-1 differs; DVD is MPEG-2.)
            if i + 14 > n:
                break
            stuffing = data[i + 13] & 0x07
            i += 14 + stuffing
            continue

        # All other codes here are PES-style: 0x000001 + stream_id + 16-bit length.
        if i + 6 > n:
            break
        pes_len = (data[i + 4] << 8) | data[i + 5]
        body_start = i + 6
        body_end = body_start + pes_len
        if pes_len == 0 or body_end > n:
            # Unbounded (length 0 is legal for video in TS-derived streams) or truncated
            # -> resync rather than trust a bad length.
            nxt = data.find(b"\x00\x00\x01", i + 4)
            if nxt < 0:
                break
            i = nxt
            continue

        if code == stream_id:
            payload = _strip_pes_header(data, body_start, body_end)
            if payload is not None:
                out += data[payload:body_end]
        # else: system header, audio/other PES, nav packs -> skip the whole packet.
        i = body_end

    return bytes(out)


def _strip_pes_header(data: bytes, start: int, end: int) -> Optional[int]:
    """Return the offset where the ES payload begins inside a PES packet body.

    Handles the MPEG-2 PES header ('10' marker, 2 flag bytes, PES_header_data_length
    + that many bytes). Raw-body streams have no header. Returns None on malformation.
    """
    if start >= end:
        return None
    # MPEG-2 PES header begins with bits '10' in the top two bits of the next byte.
    if (data[start] & 0xC0) == 0x80:
        if start + 3 > end:
            return None
        header_data_len = data[start + 2]
        payload = start + 3 + header_data_len
        return payload if payload <= end else None
    # MPEG-1-style PES header (ISO 11172-1): leading 0xFF stuffing, then an
    # optional STD_buffer field (2 bytes, top-2-bits '01'), then one of:
    # PTS-only (5 bytes, top-4-bits '0010'), PTS+DTS (10 bytes, '0011'), or the
    # no-timestamp marker byte 0x0F (1 byte). These header bytes are NOT ES
    # payload and must be skipped: the repo's own MPEG-1 VCD PS pipeline
    # (mpeg2enc -f 1 / mplex -f 1) emits them, and leaking them front-loads
    # garbage before the sequence_header start code into the VLD FSM. Bound every
    # advance by `end`; return None on overrun.
    j = start
    while j < end and data[j] == 0xFF:
        j += 1
    if j >= end:
        return None
    if (data[j] & 0xC0) == 0x40:          # STD_buffer_scale + STD_buffer_size
        j += 2
        if j >= end:
            return None
    b = data[j]
    if (b & 0xF0) == 0x20:                # PTS only (5 bytes)
        j += 5
    elif (b & 0xF0) == 0x30:             # PTS + DTS (10 bytes)
        j += 10
    elif b == 0x0F:                       # no PTS/DTS: single marker byte
        j += 1
    # else: no recognized MPEG-1 header field -> payload begins at j
    #       (raw body / already-stripped stream)
    return j if j <= end else None
