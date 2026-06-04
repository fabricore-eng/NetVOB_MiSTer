"""Real-DVD-dump validation — SKIPS cleanly when the local fixture is absent.

This exercises the nav-strip walker and the IFO parser against a REAL DVD slice
(real pack/PES structure, real VOBU PCI/DSI nav packets, real VMGI/VTSI tables)
that synthetic fixtures can't fully mimic. The fixture is **copyrighted,
local-only** test input and is NEVER committed: this test reads it from a path
outside the repo and is skipped on clean clones / CI so the suite stays green
without it.

Fixture (provide locally to run): a ``VIDEO_TS/`` with all IFOs plus a slice of
the main feature VOB, at::

    /tmp/kungpow_slice/VIDEO_TS/

Override with the ``NETVOB_REAL_DVD_DIR`` env var.
"""

import service.tests._bootstrap  # noqa: F401

import os
import unittest

from service.sources.dvddump.dvddump import (
    DVDDumpSource,
    PRIVATE_STREAM_2,
    iter_ps_units,
    strip_nav_packets,
)
from service.sources.dvddump.ifo import (
    parse_vmgi,
    parse_vtsi,
    read_ifo,
    vts_ifo_path,
)

REAL_DIR = os.environ.get(
    "NETVOB_REAL_DVD_DIR", "/tmp/kungpow_slice/VIDEO_TS"
)
VMG_IFO = os.path.join(REAL_DIR, "VIDEO_TS.IFO")
# The provided slice is the main feature's first VOB (renamed .slice.vob so it
# is unmistakably a partial, copyrighted excerpt).
SLICE_VOB = os.path.join(REAL_DIR, "VTS_10_1.slice.vob")

_HAVE_DIR = os.path.isdir(REAL_DIR) and os.path.isfile(VMG_IFO)
_HAVE_VOB = os.path.isfile(SLICE_VOB)


@unittest.skipUnless(
    _HAVE_DIR, f"real DVD fixture absent ({REAL_DIR}); skipping (clones/CI ok)"
)
class RealIfoTest(unittest.TestCase):
    def test_vmgi_identifies_disc_and_title_sets(self):
        vmgi = parse_vmgi(read_ifo(VMG_IFO))
        self.assertTrue(vmgi.is_valid)
        self.assertEqual(vmgi.identifier, b"DVDVIDEO-VMG")
        # Real disc has 11 title sets and 16 titles in the TT_SRPT.
        self.assertEqual(vmgi.nr_of_title_sets, 11)
        self.assertEqual(len(vmgi.titles), 16)
        # Title 1 is the main feature in VTS 10 with 29 chapters.
        t1 = vmgi.titles[0]
        self.assertEqual(t1.vts_nr, 10)
        self.assertEqual(t1.nr_of_chapters, 29)

    def test_vtsi_main_feature_duration(self):
        p = vts_ifo_path(REAL_DIR, 10)
        self.assertIsNotNone(p)
        vtsi = parse_vtsi(read_ifo(p))
        self.assertTrue(vtsi.is_valid)
        self.assertEqual(vtsi.nr_of_pgcs, 1)
        self.assertEqual(vtsi.nr_of_programs, 29)
        # ~81m25s feature (NTSC). Generous window: 80-83 minutes.
        self.assertIsNotNone(vtsi.duration_s)
        self.assertTrue(4800 < vtsi.duration_s < 4980, vtsi.duration_s)
        self.assertAlmostEqual(vtsi.fps, 30000 / 1001, places=6)

    def test_browse_returns_real_titles_with_main_feature(self):
        src = DVDDumpSource(root=REAL_DIR)
        entries = src.browse()
        self.assertEqual(len(entries), 16)
        self.assertTrue(all(e.extra.get("source") == "ifo" for e in entries))
        mains = [e for e in entries if e.extra.get("main_feature")]
        self.assertEqual(len(mains), 1)
        self.assertEqual(mains[0].extra["vts_nr"], 10)
        self.assertEqual(mains[0].extra["chapters"], 29)
        self.assertGreater(mains[0].duration_s, 4800)


@unittest.skipUnless(
    _HAVE_VOB, f"real VOB slice absent ({SLICE_VOB}); skipping (clones/CI ok)"
)
class RealNavStripTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with open(SLICE_VOB, "rb") as fh:
            cls.raw = fh.read()

    def test_walks_real_vob_without_parse_error(self):
        # The whole 30 MB slice parses as a PS with no PSParseError, and
        # (begin,end) tiling reconstructs the input exactly.
        n = 0
        last_end = 0
        for sid, begin, end in iter_ps_units(self.raw):
            self.assertEqual(begin, last_end)  # contiguous, no gaps
            self.assertGreater(end, begin)
            last_end = end
            n += 1
        self.assertEqual(last_end, len(self.raw))  # covered every byte
        self.assertGreater(n, 1000)

    def test_strip_is_lossless_and_removes_only_bf(self):
        # Count 0xBF nav packets in the input; stripped output must contain
        # none, must equal the concat of all non-0xBF units, and the byte
        # delta must equal exactly the removed nav-packet bytes.
        removed_count = 0
        removed_bytes = 0
        expected = bytearray()
        for sid, begin, end in iter_ps_units(self.raw):
            if sid == PRIVATE_STREAM_2:
                removed_count += 1
                removed_bytes += end - begin
            else:
                expected += self.raw[begin:end]

        clean = strip_nav_packets(self.raw)
        self.assertEqual(clean, bytes(expected))
        self.assertEqual(len(self.raw) - len(clean), removed_bytes)
        # Real slice has nav packets to remove (sanity that this isn't a no-op).
        self.assertGreater(removed_count, 0)
        # No 0xBF survives.
        self.assertFalse(
            any(sid == PRIVATE_STREAM_2 for sid, _, _ in iter_ps_units(clean))
        )

    def test_stripped_output_has_video_and_audio_units(self):
        # The audio/video spine is preserved: real slice carries video (0xE0)
        # and private_stream_1 (0xBD, the AC-3/subpicture carrier).
        clean = strip_nav_packets(self.raw)
        sids = {sid for sid, _, _ in iter_ps_units(clean)}
        self.assertIn(0xE0, sids)  # MPEG-2 video
        self.assertIn(0xBD, sids)  # private_stream_1 (AC-3 audio)


if __name__ == "__main__":
    unittest.main()
