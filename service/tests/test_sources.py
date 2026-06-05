"""Source-ABC conformance for DVDDumpSource and PlexSource (stub behavior)."""

import service.tests._bootstrap  # noqa: F401

import os
import tempfile
import threading
import unittest

from service.sources.base.source import NavInfo, Source
from service.sources.dvddump.dvddump import (
    DVDCellStreamHandle,
    DVDDumpSource,
    navinfo_from_cells,
)
from service.sources.dvddump.ifo import SECTOR, CellPlayback, CellSpan
from service.sources.plex.plex import PlexSource
from service.tests.test_ifo import build_pgc, build_vtsi_full


PREFIX = b"\x00\x00\x01"


def _pes(sid, payload):
    return PREFIX + bytes([sid]) + len(payload).to_bytes(2, "big") + payload


def _pack(payload):
    """A minimal MPEG-2 pack header (14 bytes, no stuffing) + the payload."""
    hdr = bytearray(PREFIX + b"\xba" + bytes(10))
    hdr[4] = 0x44  # top 2 bits 0b01 => MPEG-2 pack form
    hdr[13] = 0x00  # pack_stuffing_length = 0
    return bytes(hdr) + payload


def _sector(*pes_units):
    """Build a 2048-byte sector: a pack header + the PES units, zero-padded.

    Pads with a padding-stream (0xBE) PES so the whole 2048 bytes is valid PS
    that the walker tiles exactly (DVD packs are sector-sized).
    """
    body = _pack(b"") + b"".join(pes_units)
    pad_needed = SECTOR - len(body) - 6  # 6-byte PES header for the padding
    assert pad_needed >= 0, "sector overflow in test fixture"
    body += _pes(0xBE, b"\x00" * pad_needed)
    assert len(body) == SECTOR, len(body)
    return body


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


class DVDCellStreamTest(unittest.TestCase):
    """Ordered-cell streaming + nav-strip + seek on SYNTHETIC sector data."""

    def _three_cell_vob(self):
        # Three sectors, each its own cell. Each sector carries a video PES with
        # a distinct marker plus a 0xBF nav packet that MUST be stripped.
        s0 = _sector(_pes(0xE0, b"CELL0-VID"), _pes(0xBF, b"NAV0"))
        s1 = _sector(_pes(0xE0, b"CELL1-VID"), _pes(0xBF, b"NAV1"))
        s2 = _sector(_pes(0xE0, b"CELL2-VID"), _pes(0xBF, b"NAV2"))
        return s0, s1, s2

    def test_streams_cells_in_order_stripping_nav(self):
        with tempfile.TemporaryDirectory() as d:
            s0, s1, s2 = self._three_cell_vob()
            vob = os.path.join(d, "VTS_05_1.VOB")
            with open(vob, "wb") as f:
                f.write(s0 + s1 + s2)
            cells = [
                CellPlayback(1, 0x02, 0, 5.0, 0, 0, 0, vob_id=1, cell_id=1),
                CellPlayback(2, 0x08, 0, 5.0, 1, 1, 1, vob_id=1, cell_id=2),
                CellPlayback(3, 0x0A, 0, 5.0, 2, 2, 2, vob_id=2, cell_id=1),
            ]
            spans = [
                CellSpan(vob, c.first_sector * SECTOR,
                         (c.last_sector + 1) * SECTOR, c.cell_nr)
                for c in cells
            ]
            h = DVDCellStreamHandle(spans, cells=cells,
                                    nav=navinfo_from_cells(cells))
            out = bytearray()
            while True:
                chunk = h.read(100)
                if not chunk:
                    break
                out += chunk
            # Video markers appear in cell order; no NAV bytes survive.
            self.assertIn(b"CELL0-VID", out)
            i0 = out.index(b"CELL0-VID")
            i1 = out.index(b"CELL1-VID")
            i2 = out.index(b"CELL2-VID")
            self.assertLess(i0, i1)
            self.assertLess(i1, i2)
            self.assertNotIn(b"NAV0", out)
            self.assertNotIn(b"NAV1", out)
            self.assertNotIn(b"NAV2", out)

    def test_seek_resumes_at_cell_boundary(self):
        with tempfile.TemporaryDirectory() as d:
            s0, s1, s2 = self._three_cell_vob()
            vob = os.path.join(d, "VTS_05_1.VOB")
            with open(vob, "wb") as f:
                f.write(s0 + s1 + s2)
            cells = [
                CellPlayback(1, 0x02, 0, 5.0, 0, 0, 0),
                CellPlayback(2, 0x08, 0, 5.0, 1, 1, 1),
                CellPlayback(3, 0x0A, 0, 5.0, 2, 2, 2),
            ]
            spans = [
                CellSpan(vob, c.first_sector * SECTOR,
                         (c.last_sector + 1) * SECTOR, c.cell_nr)
                for c in cells
            ]
            nav = navinfo_from_cells(cells)
            # cell start times: 0.0, 5.0, 10.0
            self.assertEqual([t for t, _ in nav.entries], [0.0, 5.0, 10.0])
            h = DVDCellStreamHandle(spans, cells=cells, nav=nav)
            h.seek(7.0)  # nearest preceding -> cell 2 (t=5.0)
            out = h.read(10_000)
            self.assertNotIn(b"CELL0-VID", out)  # cell 1 skipped
            self.assertIn(b"CELL1-VID", out)
            self.assertIn(b"CELL2-VID", out)

    def test_open_dispatches_to_cell_path_when_ifo_present(self):
        # A VTS id with a matching VTS_nn_0.IFO drives the ordered-cell path
        # (assembled from a crafted IFO + single-sector VOB).
        with tempfile.TemporaryDirectory() as d:
            # One cell, one sector.
            sector = _sector(_pes(0xE0, b"FEATURE"), _pes(0xBF, b"NAVX"))
            with open(os.path.join(d, "VTS_07_1.VOB"), "wb") as f:
                f.write(sector)
            pgc = build_pgc(
                [{"first_sector": 0, "last_sector": 0, "cell_id": 1}],
                program_map=[1],
            )
            with open(os.path.join(d, "VTS_07_0.IFO"), "wb") as f:
                f.write(build_vtsi_full(pgc))
            # browse-style VMG not needed for open(); call open() directly.
            src = DVDDumpSource(root=d)
            h = src.open("dvddump:VTS_07_1")
            self.assertIsInstance(h, DVDCellStreamHandle)
            out = h.read(10_000)
            self.assertIn(b"FEATURE", out)
            self.assertNotIn(b"NAVX", out)

    def test_bounded_chunk_read_matches_whole_span(self):
        # The handle reads each cell span in bounded sector-aligned chunks (not
        # the whole ~hundreds-of-MB cell at once). Forcing a tiny chunk size so
        # a multi-sector cell spans several _fill() calls, the concatenated
        # nav-stripped output must be byte-identical to stripping the whole
        # span in one go (DVD packs are sector-sized, so chunk boundaries fall
        # on pack boundaries).
        from service.sources.dvddump.dvddump import (
            DVDCellStreamHandle,
            strip_nav_packets,
        )

        with tempfile.TemporaryDirectory() as d:
            sectors = b"".join(
                _sector(
                    _pes(0xE0, bytes([i]) + b"VID" * 8),
                    _pes(0xBF, b"NAV" + bytes([i])),  # nav -> must be stripped
                )
                for i in range(6)
            )
            vob = os.path.join(d, "VTS_05_1.VOB")
            with open(vob, "wb") as f:
                f.write(sectors)
            cells = [CellPlayback(1, 0x02, 0, 5.0, 0, 0, 5)]  # 6 sectors
            spans = [CellSpan(vob, 0, 6 * SECTOR, 1)]
            whole = strip_nav_packets(sectors)  # reference: strip the whole span

            class _SmallChunk(DVDCellStreamHandle):
                _READ_BYTES = SECTOR  # 1 sector/_fill -> forces multi-chunk

            h = _SmallChunk(spans, cells=cells, nav=navinfo_from_cells(cells))
            try:
                out = bytearray()
                while True:
                    chunk = h.read(100)  # small reads also exercise buf slicing
                    if not chunk:
                        break
                    out += chunk
            finally:
                h.close()
            self.assertEqual(bytes(out), whole)  # chunked == whole-span strip
            self.assertNotIn(b"NAV", bytes(out))
            self.assertIn(b"VID", bytes(out))

    def test_navinfo_unspecified_fps_keeps_seek_map_monotonic(self):
        # A cell with no PGC playback time (unspecified/zero fps) must still
        # advance the cumulative seek-map time, or it and the following cell
        # collapse to the SAME timestamp -> a non-monotonic map where the next
        # cell is unreachable by a time seek (it mis-lands on this one).
        cells = [
            CellPlayback(1, 0x02, 0, 10.0, 0, 0, 9),    # 10 sectors, real fps
            CellPlayback(2, 0x02, 0, None, 10, 10, 19),  # 10 sectors, UNSPEC
            CellPlayback(3, 0x02, 0, 10.0, 20, 20, 29),  # 10 sectors, real fps
        ]
        nav = navinfo_from_cells(cells)
        times = [t for t, _ in nav.entries]
        # Strictly increasing: no two cells share a timestamp.
        for a, b in zip(times, times[1:]):
            self.assertLess(a, b)
        # Cell 3 is reachable: a seek at its (estimated) start time lands on
        # cell 3's offset, not cell 2's.
        self.assertEqual(nav.nearest_preceding(times[2]), nav.entries[2])
        # The estimate is proportional to size (10 sectors) and small (~0.03s
        # at the nominal bitrate) — it bridges the gap without distorting the
        # real-fps cells around it.
        self.assertGreater(times[2], times[1])
        self.assertLess(times[2] - times[1], 1.0)

    def test_read_and_seek_are_mutually_exclusive(self):
        # The server's control thread can seek() while the streamer pump is
        # inside read(); both mutate (_buf, _buf_pos, _span_idx), so without a
        # lock they race and mix pre-/post-seek bytes (garbled PS). Prove the
        # handle serializes them: a read() held mid-_fill() must block a
        # concurrent seek() until the read completes.
        with tempfile.TemporaryDirectory() as d:
            s0, s1, s2 = self._three_cell_vob()
            vob = os.path.join(d, "VTS_05_1.VOB")
            with open(vob, "wb") as f:
                f.write(s0 + s1 + s2)
            cells = [
                CellPlayback(1, 0x02, 0, 5.0, 0, 0, 0),
                CellPlayback(2, 0x08, 0, 5.0, 1, 1, 1),
                CellPlayback(3, 0x0A, 0, 5.0, 2, 2, 2),
            ]
            spans = [
                CellSpan(vob, c.first_sector * SECTOR,
                         (c.last_sector + 1) * SECTOR, c.cell_nr)
                for c in cells
            ]
            nav = navinfo_from_cells(cells)

            in_fill = threading.Event()
            release_fill = threading.Event()

            class _BlockingFillHandle(DVDCellStreamHandle):
                def _fill(self):
                    # Signal we're inside read()'s critical section, then hold
                    # it until the test releases us.
                    in_fill.set()
                    release_fill.wait(timeout=5.0)
                    return super()._fill()

            h = _BlockingFillHandle(spans, cells=cells, nav=nav)

            read_done = threading.Event()
            seek_done = threading.Event()

            def _do_read():
                h.read(100)
                read_done.set()

            def _do_seek():
                h.seek(7.0)
                seek_done.set()

            reader = threading.Thread(target=_do_read)
            reader.start()
            self.assertTrue(in_fill.wait(timeout=5.0))  # read holds the lock

            seeker = threading.Thread(target=_do_seek)
            seeker.start()
            # The seek must NOT complete while read holds the lock.
            self.assertFalse(
                seek_done.wait(timeout=0.5),
                "seek() ran concurrently with read() (no mutual exclusion)",
            )

            # Release read; both should finish promptly and in order.
            release_fill.set()
            self.assertTrue(seek_done.wait(timeout=5.0))
            self.assertTrue(read_done.wait(timeout=5.0))
            reader.join(timeout=5.0)
            seeker.join(timeout=5.0)


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
