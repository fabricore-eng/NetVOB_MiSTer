"""Source-ABC conformance for DVDDumpSource and PlexSource (stub behavior)."""

import service.tests._bootstrap  # noqa: F401

import os
import tempfile
import unittest

from service.sources.base.source import NavInfo, Source
from service.sources.dvddump.dvddump import DVDDumpSource
from service.sources.plex.plex import PlexSource


PREFIX = b"\x00\x00\x01"


def _pes(sid, payload):
    return PREFIX + bytes([sid]) + len(payload).to_bytes(2, "big") + payload


class DVDDumpSourceTest(unittest.TestCase):
    def test_identity_metadata(self):
        src = DVDDumpSource()
        self.assertIsInstance(src, Source)
        self.assertEqual(src.name, "DVD Dumps")
        self.assertEqual(src.badge, "field-exact")
        self.assertFalse(src.is_local_to_console)

    def test_open_bytes_strips_nav_and_reads_exactly(self):
        src = DVDDumpSource()
        video = _pes(0xE0, b"VIDEO-DATA")
        nav = _pes(0xBF, b"NAV-DROP")
        raw = video + nav
        h = src.open_bytes(raw, already_stripped=False)
        out = h.read(10_000)
        self.assertEqual(out, video)  # nav gone, video preserved exactly
        self.assertEqual(h.av.video, "mpeg2")

    def test_open_bytes_read_chunks_and_eof(self):
        src = DVDDumpSource()
        data = bytes(range(100))
        h = src.open_bytes(data)
        first = h.read(40)
        second = h.read(40)
        third = h.read(40)
        fourth = h.read(40)
        self.assertEqual(first + second + third, data)
        self.assertEqual(len(first), 40)
        self.assertEqual(len(third), 20)
        self.assertEqual(fourth, b"")  # EOF

    def test_seek_uses_nav_index(self):
        src = DVDDumpSource()
        data = b"0123456789" * 10  # 100 bytes
        nav = NavInfo(entries=[(0.0, 0), (5.0, 30), (10.0, 70)])
        h = src.open_bytes(data, nav=nav)
        h.seek(7.5)  # nearest preceding -> (5.0, 30)
        self.assertEqual(h.read(5), data[30:35])

    def test_browse_enumerates_vob_stub(self):
        with tempfile.TemporaryDirectory() as d:
            for nm in ("VTS_01_1.VOB", "VTS_01_2.VOB", "VIDEO_TS.IFO"):
                with open(os.path.join(d, nm), "wb") as f:
                    f.write(b"\x00")
            src = DVDDumpSource(root=d)
            entries = src.browse()
            ids = [e.id for e in entries]
            self.assertEqual(
                ids, ["dvddump:VTS_01_1", "dvddump:VTS_01_2"]
            )  # .IFO excluded, sorted
            self.assertTrue(all(e.badge == "field-exact" for e in entries))
            self.assertTrue(all(e.extra.get("stub") for e in entries))

    def test_open_reads_file_and_strips(self):
        with tempfile.TemporaryDirectory() as d:
            raw = _pes(0xE0, b"keep") + _pes(0xBF, b"strip")
            with open(os.path.join(d, "VTS_01_1.VOB"), "wb") as f:
                f.write(raw)
            src = DVDDumpSource(root=d)
            h = src.open("dvddump:VTS_01_1")
            self.assertEqual(h.read(10_000), _pes(0xE0, b"keep"))

    def test_open_missing_raises(self):
        src = DVDDumpSource(root="/nonexistent-dir-xyz")
        with self.assertRaises(FileNotFoundError):
            src.open("dvddump:nope")

    def test_health_unconfigured_and_configured(self):
        self.assertFalse(DVDDumpSource().health()["configured"])
        with tempfile.TemporaryDirectory() as d:
            self.assertTrue(DVDDumpSource(root=d).health()["configured"])


class PlexSourceStubTest(unittest.TestCase):
    def test_identity_metadata(self):
        src = PlexSource()
        self.assertIsInstance(src, Source)
        self.assertEqual(src.name, "Plex")
        self.assertEqual(src.badge, "transcoded")
        self.assertFalse(src.is_local_to_console)

    def test_browse_empty(self):
        self.assertEqual(PlexSource().browse(), [])

    def test_open_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            PlexSource().open("plex:1")

    def test_health_unconfigured_note(self):
        h = PlexSource(url=None, token=None).health()
        self.assertFalse(h["configured"])
        self.assertFalse(h["reachable"])
        self.assertIn("PLEX_URL", h["note"])
        self.assertIn("PLEX_TOKEN", h["note"])

    def test_health_configured_reports_stub(self):
        h = PlexSource(url="http://plex:32400", token="tok").health()
        self.assertTrue(h["configured"])
        self.assertFalse(h["reachable"])  # still a stub


if __name__ == "__main__":
    unittest.main()
