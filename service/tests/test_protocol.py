"""Control protocol round-trips each control message + the media preamble.

Asserts exact decoded values, not just "parses".
"""

import service.tests._bootstrap  # noqa: F401

import unittest

from service.core import protocol as p
from service.sources.base.source import AvInfo


class ControlRoundTripTest(unittest.TestCase):
    def test_each_control_message_round_trips(self):
        cases = [
            p.make(p.MSG_BROWSE),
            p.make(p.MSG_BROWSE, source="DVD Dumps", path="/dumps"),
            p.make(p.MSG_PLAY, id="dvddump:matrix-1999"),
            p.make(p.MSG_PLAY, id="plex:12345", source="Plex"),
            p.make(p.MSG_PAUSE),
            p.make(p.MSG_RESUME),
            p.make(p.MSG_SEEK, t=42.5),
            p.make(p.MSG_SEEK, t=0),
            p.make(p.MSG_STOP),
            p.make(p.MSG_STATUS),
        ]
        for msg in cases:
            with self.subTest(type=msg["type"]):
                wire = p.encode(msg)
                self.assertTrue(wire.endswith(b"\n"))
                self.assertEqual(p.decode(wire), msg)

    def test_play_requires_id(self):
        with self.assertRaises(p.ProtocolError):
            p.make(p.MSG_PLAY)
        with self.assertRaises(p.ProtocolError):
            p.decode(b'{"type":"play"}')

    def test_seek_requires_numeric_t(self):
        with self.assertRaises(p.ProtocolError):
            p.make(p.MSG_SEEK)
        with self.assertRaises(p.ProtocolError):
            p.make(p.MSG_SEEK, t="later")
        with self.assertRaises(p.ProtocolError):
            p.make(p.MSG_SEEK, t=True)  # bool is not a valid time

    def test_seek_exact_value_preserved(self):
        msg = p.decode(p.encode(p.make(p.MSG_SEEK, t=123.456)))
        self.assertEqual(msg["t"], 123.456)
        self.assertEqual(msg["type"], "seek")

    def test_decode_rejects_non_object_and_empty(self):
        with self.assertRaises(p.ProtocolError):
            p.decode(b"")
        with self.assertRaises(p.ProtocolError):
            p.decode(b"[1,2,3]")
        with self.assertRaises(p.ProtocolError):
            p.decode(b"not json")

    def test_decode_stream_frames_multiple_messages(self):
        wire = (
            p.encode(p.make(p.MSG_PLAY, id="dvddump:x"))
            + p.encode(p.make(p.MSG_PAUSE))
            + b'{"type":"stop"}'  # trailing partial (no newline)
        )
        msgs, remainder = p.decode_stream(wire)
        self.assertEqual(len(msgs), 2)
        self.assertEqual(msgs[0]["type"], "play")
        self.assertEqual(msgs[0]["id"], "dvddump:x")
        self.assertEqual(msgs[1]["type"], "pause")
        self.assertEqual(remainder, b'{"type":"stop"}')


class PreambleTest(unittest.TestCase):
    def test_preamble_round_trips_with_exact_fields(self):
        av = AvInfo(
            video="mpeg2",
            audio="ac3",
            pts_base=12345,
            field_cadence="interlaced",
        )
        pre = p.make_preamble(
            stream_id="sess-001",
            av=av,
            duration_s=5400.0,
            nav_available=True,
        )
        wire = p.encode_preamble(pre)
        self.assertTrue(wire.endswith(b"\n"))

        got = p.decode_preamble(wire)
        self.assertEqual(got["type"], "session")
        self.assertEqual(got["stream_id"], "sess-001")
        self.assertEqual(got["duration_s"], 5400.0)
        self.assertTrue(got["nav_available"])
        self.assertEqual(
            got["av"],
            {
                "video": "mpeg2",
                "audio": "ac3",
                "pts_base": 12345,
                "field_cadence": "interlaced",
            },
        )

    def test_preamble_no_nav_no_duration(self):
        pre = p.make_preamble(
            stream_id="plex-7",
            av=AvInfo(audio="mp2"),
            duration_s=None,
            nav_available=False,
        )
        got = p.decode_preamble(p.encode_preamble(pre))
        self.assertIsNone(got["duration_s"])
        self.assertFalse(got["nav_available"])
        self.assertEqual(got["av"]["audio"], "mp2")

    def test_encode_preamble_rejects_non_session(self):
        with self.assertRaises(p.ProtocolError):
            p.encode_preamble({"type": "browse"})


if __name__ == "__main__":
    unittest.main()
