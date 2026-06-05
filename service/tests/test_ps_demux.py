"""PS->ES demux: recovers EXACTLY the video elementary stream from a Program Stream.

Hand-builds MPEG-2 PS packets (pack, system_header, video/audio PES, DVD nav pack)
and asserts the demux concatenates only the chosen video stream's payloads, byte-exact
— including PES headers with PTS, ES split across packets, leading garbage resync, and
skipping audio/nav/system. References ISO/IEC 13818-1 §2.5 / §2.4.3.6.
"""

import service.tests._bootstrap  # noqa: F401

import unittest

from service.core.ps_demux import demux_ps_to_video_es, VIDEO_STREAM_0


def pack() -> bytes:
    """MPEG-2 pack_header: 0x000001BA + 10 bytes; last byte's low 3 bits = stuffing(0)."""
    return b"\x00\x00\x01\xBA" + bytes([0x44, 0x00, 0x04, 0x00, 0x04, 0x01, 0x00, 0x00, 0x00, 0xF8])


def system_header() -> bytes:
    body = b"\x80\x00"  # contents irrelevant; demux skips the whole packet
    return b"\x00\x00\x01\xBB" + bytes([len(body) >> 8, len(body) & 0xFF]) + body


def video_pes(payload: bytes, sid: int = VIDEO_STREAM_0, pts: bool = False) -> bytes:
    if pts:
        # PES header with a 5-byte PTS field: flags 0x80 -> PTS present, hdr_data_len=5.
        hdr = bytes([0x80, 0x80, 0x05]) + b"\x21\x00\x01\x00\x01"
    else:
        hdr = bytes([0x80, 0x00, 0x00])  # '10' marker, no flags, hdr_data_len=0
    body = hdr + payload
    return b"\x00\x00\x01" + bytes([sid, len(body) >> 8, len(body) & 0xFF]) + body


def audio_pes(payload: bytes) -> bytes:
    return video_pes(payload, sid=0xC0)  # audio stream 0 -> 0xC0


def nav_pack() -> bytes:
    """DVD nav pack lives in private_stream_2 (0xBF): raw body, no PES header."""
    body = b"\xAA" * 8
    return b"\x00\x00\x01\xBF" + bytes([len(body) >> 8, len(body) & 0xFF]) + body


def mpeg1_video_pes(payload: bytes, sid: int = VIDEO_STREAM_0, *,
                    stuffing: int = 0, std: bool = False, ts: str = "none") -> bytes:
    """MPEG-1-form video PES (ISO 11172-1, NO MPEG-2 '10' marker):
    [0xFF*stuffing][STD_buffer 2B][PTS 5B | PTS+DTS 10B | 0x0F 1B] + payload.
    These header bytes precede the ES payload and must be stripped. The repo's
    own MPEG-1 VCD pipeline (mpeg2enc -f 1 / mplex -f 1) emits this form."""
    hdr = b"\xFF" * stuffing
    if std:
        hdr += b"\x42\x00"                                  # STD_buffer (top bits '01')
    if ts == "pts":
        hdr += b"\x21\x00\x01\x00\x01"                      # PTS only, 5B ('0010')
    elif ts == "ptsdts":
        hdr += b"\x31\x00\x01\x00\x01\x11\x00\x01\x00\x01"  # PTS+DTS, 10B ('0011')
    elif ts == "0f":
        hdr += b"\x0F"                                      # no PTS/DTS, 1B marker
    body = hdr + payload
    return b"\x00\x00\x01" + bytes([sid, len(body) >> 8, len(body) & 0xFF]) + body


PROGRAM_END = b"\x00\x00\x01\xB9"

# A plausible video ES fragment (starts with a sequence_header_code, like a real clip).
ES_A = b"\x00\x00\x01\xB3\x2d\x01\xe0\x14\x12\x34\x56\x78"
ES_B = b"\x00\x00\x01\x00\x00\x0f\xff\xf8\xde\xad\xbe\xef"


class TestPsDemux(unittest.TestCase):
    def test_extracts_only_video_in_order(self):
        ps = (
            pack() + system_header()
            + video_pes(ES_A) + audio_pes(b"AUDIO!") + nav_pack()
            + video_pes(ES_B) + PROGRAM_END
        )
        self.assertEqual(demux_ps_to_video_es(ps), ES_A + ES_B)

    def test_skips_audio_and_nav_and_system(self):
        ps = pack() + system_header() + audio_pes(b"justaudio") + nav_pack()
        self.assertEqual(demux_ps_to_video_es(ps), b"")

    def test_pes_header_with_pts_is_skipped(self):
        # The 5-byte PTS header bytes must NOT leak into the ES.
        ps = pack() + video_pes(ES_A, pts=True)
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)

    def test_es_split_across_two_pes_is_contiguous(self):
        whole = ES_A + ES_B
        mid = len(ES_A)
        ps = pack() + video_pes(whole[:mid]) + video_pes(whole[mid:]) + PROGRAM_END
        self.assertEqual(demux_ps_to_video_es(ps), whole)

    def test_leading_garbage_resync(self):
        ps = b"\xff\xff\x00\x42garbage" + pack() + video_pes(ES_A)
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)

    def test_alternate_video_stream_id(self):
        # stream 0xE1 selected; 0xE0 must be ignored.
        ps = pack() + video_pes(ES_A, sid=0xE0) + video_pes(ES_B, sid=0xE1)
        self.assertEqual(demux_ps_to_video_es(ps, stream_id=0xE1), ES_B)

    def test_empty_and_no_video(self):
        self.assertEqual(demux_ps_to_video_es(b""), b"")
        self.assertEqual(demux_ps_to_video_es(pack() + PROGRAM_END), b"")

    # --- MPEG-1-form PES header regression (spine-review bug #2) ---
    # The MPEG-1 PES header (STD_buffer / PTS / PTS+DTS / 0x0F) must be stripped;
    # leaking it front-loads garbage before the sequence_header into the VLD.
    def test_mpeg1_pts_header_skipped(self):
        ps = pack() + mpeg1_video_pes(ES_A, ts="pts") + PROGRAM_END
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)

    def test_mpeg1_ptsdts_header_skipped(self):
        ps = pack() + mpeg1_video_pes(ES_A, ts="ptsdts") + PROGRAM_END
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)

    def test_mpeg1_std_buffer_plus_pts_skipped(self):
        ps = pack() + mpeg1_video_pes(ES_A, std=True, ts="pts") + PROGRAM_END
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)

    def test_mpeg1_stuffing_plus_pts_skipped(self):
        ps = pack() + mpeg1_video_pes(ES_A, stuffing=3, ts="pts") + PROGRAM_END
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)

    def test_mpeg1_0f_marker_skipped(self):
        ps = pack() + mpeg1_video_pes(ES_A, ts="0f") + PROGRAM_END
        self.assertEqual(demux_ps_to_video_es(ps), ES_A)


if __name__ == "__main__":
    unittest.main()
