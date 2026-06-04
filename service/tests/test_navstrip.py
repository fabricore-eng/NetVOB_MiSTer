"""Nav-pack stripping: removes 0xBF packets, preserves every other byte exactly.

Fixtures are built byte-for-byte inside the tests (no external assets) so the
assertions pin down EXACT values, not "looks right".
"""

import service.tests._bootstrap  # noqa: F401  (sys.path side effect)

import unittest

from service.sources.dvddump.dvddump import (
    PRIVATE_STREAM_2,
    PSParseError,
    iter_ps_units,
    strip_nav_packets,
)

PREFIX = b"\x00\x00\x01"


def pes(stream_id: int, payload: bytes) -> bytes:
    """Build a length-prefixed PES/system packet: 00 00 01 sid LL LL <payload>."""
    assert len(payload) <= 0xFFFF
    return PREFIX + bytes([stream_id]) + len(payload).to_bytes(2, "big") + payload


def mpeg2_pack_header(stuffing: int = 0) -> bytes:
    """Build a valid 14+stuffing-byte MPEG-2 pack header (00 00 01 BA ...).

    Byte 4's top two bits must be ``01`` to mark the MPEG-2 form; byte 13's low
    3 bits carry ``pack_stuffing_length``. Exact SCR/mux values are irrelevant
    to the walker, so we use deterministic filler that keeps the marker bits.
    """
    assert 0 <= stuffing <= 7
    hdr = bytearray(PREFIX + b"\xba")
    # byte 4: 0b01xxxxxx  -> 0x44 keeps top bits '01'
    hdr += bytes([0x44])
    # bytes 5..12: arbitrary deterministic filler (8 bytes)
    hdr += bytes(range(0x10, 0x18))
    # byte 13: pack_stuffing_length in low 3 bits (top 5 are reserved '1')
    hdr += bytes([0xF8 | stuffing])
    # stuffing bytes (must be 0xFF per spec; value is irrelevant to walker)
    hdr += b"\xff" * stuffing
    assert len(hdr) == 14 + stuffing
    return bytes(hdr)


PROGRAM_END = PREFIX + b"\xb9"


class StripNavTest(unittest.TestCase):
    def test_strips_bf_preserves_everything_else_byte_for_byte(self):
        # A crafted PS: pack hdr, system hdr (0xBB), video (0xE0), audio (0xBD),
        # a DVD nav PES (0xBF) that must vanish, more video, program end.
        pack = mpeg2_pack_header(stuffing=2)
        sys_hdr = pes(0xBB, b"\x80\x00\x21" + b"\x00" * 3)  # system header
        video1 = pes(0xE0, b"\xDE\xAD\xBE\xEF\x01\x02")
        audio = pes(0xBD, b"\x80\x05private-1")  # private_stream_1 (e.g. AC3)
        nav = pes(PRIVATE_STREAM_2, b"PCI-DSI-NAV-PAYLOAD-SHOULD-BE-DROPPED")
        video2 = pes(0xE0, b"\xCA\xFE\xBA\xBE")

        buf = pack + sys_hdr + video1 + audio + nav + video2 + PROGRAM_END

        out = strip_nav_packets(buf)

        # EXACT expected output = everything except the 0xBF packet.
        expected = pack + sys_hdr + video1 + audio + video2 + PROGRAM_END
        self.assertEqual(out, expected)

        # And, byte-for-byte, the dropped region is exactly the nav packet.
        self.assertEqual(len(buf) - len(out), len(nav))
        self.assertNotIn(b"PCI-DSI-NAV-PAYLOAD-SHOULD-BE-DROPPED", out)
        # Every preserved packet's bytes are untouched.
        self.assertIn(b"\xDE\xAD\xBE\xEF\x01\x02", out)
        self.assertIn(b"\xCA\xFE\xBA\xBE", out)
        self.assertIn(b"private-1", out)

    def test_multiple_nav_packets_all_removed(self):
        pack = mpeg2_pack_header(stuffing=0)
        nav_a = pes(PRIVATE_STREAM_2, b"NAV-A" * 10)
        v = pes(0xE0, b"video-payload")
        nav_b = pes(PRIVATE_STREAM_2, b"NAV-B" * 7)
        a = pes(0xC0, b"audio-mp2")
        nav_c = pes(PRIVATE_STREAM_2, b"")  # zero-length nav packet too

        buf = pack + nav_a + v + nav_b + a + nav_c
        out = strip_nav_packets(buf)

        expected = pack + v + a
        self.assertEqual(out, expected)
        self.assertNotIn(b"NAV-A", out)
        self.assertNotIn(b"NAV-B", out)

    def test_no_nav_packets_is_identity(self):
        # If there are no 0xBF packets, output must equal input exactly.
        buf = (
            mpeg2_pack_header(stuffing=4)
            + pes(0xE0, b"\x00\x01\x02\x03")
            + pes(0xBD, b"\x80\x00ac3-frame")
            + PROGRAM_END
        )
        self.assertEqual(strip_nav_packets(buf), buf)

    def test_padding_stream_preserved(self):
        # 0xBE padding is NOT nav; it must pass through (only 0xBF is stripped).
        pad = pes(0xBE, b"\xff" * 16)
        buf = mpeg2_pack_header() + pad + pes(0xE0, b"v") + PROGRAM_END
        out = strip_nav_packets(buf)
        self.assertEqual(out, buf)

    def test_mpeg1_pack_header_walked(self):
        # MPEG-1 pack header is 12 bytes: 00 00 01 BA + 8 bytes, top nibble 0010.
        mpeg1_pack = PREFIX + b"\xba" + bytes([0x21]) + bytes(range(7))
        self.assertEqual(len(mpeg1_pack), 12)
        nav = pes(PRIVATE_STREAM_2, b"drop-me")
        buf = mpeg1_pack + pes(0xE0, b"keep") + nav
        out = strip_nav_packets(buf)
        self.assertEqual(out, mpeg1_pack + pes(0xE0, b"keep"))

    def test_walker_unit_boundaries_exact(self):
        pack = mpeg2_pack_header(stuffing=3)
        v = pes(0xE0, b"abc")
        nav = pes(PRIVATE_STREAM_2, b"xyz")
        buf = pack + v + nav + PROGRAM_END

        units = list(iter_ps_units(buf))
        # Reconstruct from reported (begin, end) -> must equal the original.
        rebuilt = b"".join(buf[b:e] for _, b, e in units)
        self.assertEqual(rebuilt, buf)
        # Start codes in order.
        self.assertEqual(
            [sid for sid, _, _ in units],
            [0xBA, 0xE0, PRIVATE_STREAM_2, 0xB9],
        )

    def test_truncated_pes_raises(self):
        # length says 100 bytes but only 3 follow -> must raise, not silently
        # corrupt the stream.
        bad = PREFIX + b"\xe0" + (100).to_bytes(2, "big") + b"\x01\x02\x03"
        with self.assertRaises(PSParseError):
            strip_nav_packets(bad)

    def test_bad_start_code_raises(self):
        with self.assertRaises(PSParseError):
            strip_nav_packets(b"\xde\xad\xbe\xef")


if __name__ == "__main__":
    unittest.main()
