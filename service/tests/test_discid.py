"""Disc content fingerprint: stable across copies, sensitive to structure.

The fingerprint must be (a) deterministic, (b) identical for a byte-for-byte copy of a
dump at a different path / different filename casing, (c) different when the IFO set or
its contents change, and (d) independent of the non-IFO payload (VOBs/BUPs). It must
also accept either the ``VIDEO_TS`` directory or a disc-root containing it, and degrade
gracefully (None) on a folder that is not a DVD-Video dump.
"""

import service.tests._bootstrap  # noqa: F401

import os
import shutil
import tempfile
import unittest

from service.sources.dvddump.discid import (
    NoDvdStructure,
    disc_fingerprint,
    disc_id,
    try_disc_id,
)


def _write(directory: str, name: str, data: bytes) -> None:
    with open(os.path.join(directory, name), "wb") as fh:
        fh.write(data)


def _make_dump(directory: str) -> None:
    """A minimal two-title-set dump: a VMGI + two VTSI IFOs, plus VOB/BUP noise."""
    _write(directory, "VIDEO_TS.IFO", b"DVDVIDEO-VMG" + b"\x00" * 500)
    _write(directory, "VTS_01_0.IFO", b"DVDVIDEO-VTS" + b"\x01" * 800)
    _write(directory, "VTS_02_0.IFO", b"DVDVIDEO-VTS" + b"\x02" * 300)
    _write(directory, "VTS_01_1.VOB", b"\xFF" * 4096)  # payload — must not affect id
    _write(directory, "VIDEO_TS.BUP", b"backup-copy")  # not an IFO — ignored


class DiscFingerprintTests(unittest.TestCase):
    def test_format_is_16_hex(self):
        with tempfile.TemporaryDirectory() as d:
            _make_dump(d)
            fp = disc_fingerprint(d)
            self.assertEqual(len(fp), 16)
            int(fp, 16)  # raises if not hex
            self.assertEqual(disc_id(d), "dvddump:disc-" + fp)

    def test_deterministic(self):
        with tempfile.TemporaryDirectory() as d:
            _make_dump(d)
            self.assertEqual(disc_fingerprint(d), disc_fingerprint(d))

    def test_stable_across_copy_to_different_path(self):
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _make_dump(a)
            for nm in os.listdir(a):
                shutil.copy2(os.path.join(a, nm), os.path.join(b, nm))
            self.assertEqual(disc_fingerprint(a), disc_fingerprint(b))

    def test_payload_does_not_affect_id(self):
        """Changing/adding VOB/BUP bytes must not change the fingerprint."""
        with tempfile.TemporaryDirectory() as d:
            _make_dump(d)
            before = disc_fingerprint(d)
            _write(d, "VTS_01_1.VOB", b"\x00" * 99999)  # different payload
            _write(d, "VTS_01_2.VOB", b"more payload")  # extra payload file
            self.assertEqual(before, disc_fingerprint(d))

    def test_ifo_content_change_changes_id(self):
        with tempfile.TemporaryDirectory() as d:
            _make_dump(d)
            before = disc_fingerprint(d)
            _write(d, "VTS_01_0.IFO", b"DVDVIDEO-VTS" + b"\x09" * 800)  # different bytes
            self.assertNotEqual(before, disc_fingerprint(d))

    def test_ifo_size_change_changes_id(self):
        with tempfile.TemporaryDirectory() as d:
            _make_dump(d)
            before = disc_fingerprint(d)
            _write(d, "VTS_02_0.IFO", b"DVDVIDEO-VTS" + b"\x02" * 301)  # one byte longer
            self.assertNotEqual(before, disc_fingerprint(d))

    def test_extra_ifo_changes_id(self):
        with tempfile.TemporaryDirectory() as d:
            _make_dump(d)
            before = disc_fingerprint(d)
            _write(d, "VTS_03_0.IFO", b"DVDVIDEO-VTS" + b"\x03" * 100)
            self.assertNotEqual(before, disc_fingerprint(d))

    def test_filename_casing_does_not_matter(self):
        """A lowercase-named copy fingerprints identically (names normalised upper)."""
        with tempfile.TemporaryDirectory() as a, tempfile.TemporaryDirectory() as b:
            _make_dump(a)
            for nm in os.listdir(a):
                shutil.copy2(os.path.join(a, nm), os.path.join(b, nm.lower()))
            self.assertEqual(disc_fingerprint(a), disc_fingerprint(b))

    def test_accepts_disc_root_with_video_ts_child(self):
        with tempfile.TemporaryDirectory() as root:
            vts = os.path.join(root, "VIDEO_TS")
            os.mkdir(vts)
            _make_dump(vts)
            # Passing the root (which contains VIDEO_TS) matches passing VIDEO_TS itself.
            self.assertEqual(disc_fingerprint(root), disc_fingerprint(vts))

    def test_no_ifos_raises_and_try_returns_none(self):
        with tempfile.TemporaryDirectory() as d:
            _write(d, "random.mp4", b"not a dvd")
            with self.assertRaises(NoDvdStructure):
                disc_fingerprint(d)
            self.assertIsNone(try_disc_id(d))

    def test_missing_path_is_handled(self):
        self.assertIsNone(try_disc_id("/nonexistent/path/to/nowhere"))
        with self.assertRaises(NoDvdStructure):
            disc_fingerprint("/nonexistent/path/to/nowhere")


class BrowseStampsDiscIdTests(unittest.TestCase):
    """DVDDumpSource.browse() stamps every entry with the disc's fingerprint."""

    def test_browse_stamps_disc_id(self):
        from service.sources.dvddump.dvddump import DVDDumpSource

        with tempfile.TemporaryDirectory() as d:
            # A VTS IFO (so try_disc_id finds structure) but no parseable VIDEO_TS.IFO,
            # so browse() takes the .VOB fallback and still stamps the disc id.
            _write(d, "VTS_01_0.IFO", b"DVDVIDEO-VTS" + b"\x01" * 200)
            _write(d, "VTS_01_1.VOB", b"\x00\x00\x01\xBA" + b"\x00" * 100)
            entries = DVDDumpSource(root=d).browse()
            self.assertTrue(entries, "expected at least one fallback entry")
            expected = disc_id(d)
            for e in entries:
                self.assertEqual(e.extra.get("disc_id"), expected)

    def test_browse_no_ifo_stamps_none(self):
        from service.sources.dvddump.dvddump import DVDDumpSource

        with tempfile.TemporaryDirectory() as d:
            _write(d, "movie.VOB", b"\x00\x00\x01\xBA" + b"\x00" * 100)
            entries = DVDDumpSource(root=d).browse()
            self.assertTrue(entries)
            for e in entries:
                self.assertIsNone(e.extra.get("disc_id"))


if __name__ == "__main__":
    unittest.main()
