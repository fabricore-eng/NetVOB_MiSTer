"""DVD IFO parsing (VMGI TT_SRPT + VTSI PGC) on SYNTHETIC crafted bytes.

Every fixture here is built byte-for-byte in-test (no external assets), so the
assertions pin EXACT structural values. A separate ``test_real_data.py`` runs
the same code on a real (copyrighted, local-only) dump and SKIPS if absent.
"""

import service.tests._bootstrap  # noqa: F401

import struct
import unittest

from service.sources.dvddump.ifo import (
    IFOParseError,
    SECTOR,
    decode_dvd_time,
    parse_vmgi,
    parse_vtsi,
)


def _u16(v: int) -> bytes:
    return struct.pack(">H", v)


def _u32(v: int) -> bytes:
    return struct.pack(">I", v)


def build_vmgi(titles, *, nr_title_sets=2, spec=(1, 1), tt_srpt_sector=1):
    """Craft a minimal VMGI buffer with a TT_SRPT at ``tt_srpt_sector``.

    ``titles`` is a list of dicts with keys: vts_nr, vts_ttn, chapters, angles,
    title_set_sector, playback_type (all optional but vts_nr).
    """
    # --- VMGI mat (first sector) ---
    mat = bytearray(SECTOR)
    mat[0:12] = b"DVDVIDEO-VMG"
    mat[0x20] = (spec[0] << 4) | spec[1]
    mat[0x3E:0x40] = _u16(nr_title_sets)
    mat[0xC4:0xC8] = _u32(tt_srpt_sector)

    # --- TT_SRPT (its own sector) ---
    nr = len(titles)
    last_byte = 8 + nr * 12 - 1
    srpt = bytearray()
    srpt += _u16(nr)
    srpt += b"\x00\x00"  # reserved
    srpt += _u32(last_byte)
    for t in titles:
        e = bytearray(12)
        e[0] = t.get("playback_type", 0x3F)
        e[1] = t.get("angles", 1)
        e[2:4] = _u16(t.get("chapters", 1))
        e[4:6] = _u16(t.get("parental", 0))
        e[6] = t["vts_nr"]
        e[7] = t.get("vts_ttn", 1)
        e[8:12] = _u32(t.get("title_set_sector", 0))
        srpt += e
    # place TT_SRPT at its sector
    buf = bytearray(mat)
    base = tt_srpt_sector * SECTOR
    if len(buf) < base + len(srpt):
        buf += bytes(base + len(srpt) - len(buf))
    buf[base : base + len(srpt)] = srpt
    return bytes(buf)


def build_vtsi(playback_time: bytes, *, nr_programs=1, nr_cells=1, nr_pgci=1,
               pgcit_sector=2):
    """Craft a minimal VTSI buffer with one PGC carrying ``playback_time``."""
    mat = bytearray(SECTOR)
    mat[0:12] = b"DVDVIDEO-VTS"
    mat[0xCC:0xD0] = _u32(pgcit_sector)

    # VTS_PGCIT: header + 1 SRP entry pointing at a PGC right after the SRPs.
    pgc_start_byte = 8 + nr_pgci * 8  # PGC begins after all SRP entries
    pgcit = bytearray()
    pgcit += _u16(nr_pgci)
    pgcit += b"\x00\x00"
    pgcit += _u32(0)  # last_byte (unused by parser beyond bounds)
    # first SRP entry (8 bytes): entry_id, reserved, ptl_mask(2), pgc_byte(4)
    srp = bytearray(8)
    srp[0] = 0x81
    srp[4:8] = _u32(pgc_start_byte)
    pgcit += srp
    # pad remaining SRP entries if nr_pgci > 1
    pgcit += bytes((nr_pgci - 1) * 8)
    # PGC: byte+2 nr_programs, +3 nr_cells, +4 playback_time(4)
    pgc = bytearray(8)
    pgc[2] = nr_programs
    pgc[3] = nr_cells
    pgc[4:8] = playback_time
    pgcit += pgc

    buf = bytearray(mat)
    base = pgcit_sector * SECTOR
    if len(buf) < base + len(pgcit):
        buf += bytes(base + len(pgcit) - len(buf))
    buf[base : base + len(pgcit)] = pgcit
    return bytes(buf)


class DvdTimeTest(unittest.TestCase):
    def test_decode_ntsc(self):
        # 01:21:25 + 24 frames @ 29.97 (fps code 0b11): real KUNGPOW value.
        s, fps = decode_dvd_time(bytes.fromhex("012125e4"))
        self.assertAlmostEqual(fps, 30000 / 1001, places=6)
        # 1*3600 + 21*60 + 25 = 4885 s, + 24/29.97 ~ 0.8009
        self.assertAlmostEqual(s, 4885 + 24 / (30000 / 1001), places=4)

    def test_decode_pal(self):
        # 00:30:00 + 0 frames @ 25 (fps code 0b01).
        s, fps = decode_dvd_time(bytes([0x00, 0x30, 0x00, 0x40]))
        self.assertEqual(fps, 25.0)
        self.assertAlmostEqual(s, 30 * 60)

    def test_decode_unspecified_fps_returns_none(self):
        # fps code 0b00 -> cannot resolve a precise duration.
        s, fps = decode_dvd_time(bytes([0x00, 0x10, 0x00, 0x00]))
        self.assertIsNone(s)
        self.assertIsNone(fps)


class VmgiTest(unittest.TestCase):
    def test_parse_titles_exact(self):
        buf = build_vmgi(
            [
                {"vts_nr": 2, "chapters": 29, "angles": 1},
                {"vts_nr": 1, "chapters": 3, "angles": 2, "vts_ttn": 1},
            ],
            nr_title_sets=2,
            spec=(1, 1),
        )
        vmgi = parse_vmgi(buf)
        self.assertTrue(vmgi.is_valid)
        self.assertEqual(vmgi.identifier, b"DVDVIDEO-VMG")
        self.assertEqual(vmgi.spec_version, (1, 1))
        self.assertEqual(vmgi.nr_of_title_sets, 2)
        self.assertEqual(len(vmgi.titles), 2)
        t0 = vmgi.titles[0]
        self.assertEqual(t0.title_nr, 1)
        self.assertEqual(t0.vts_nr, 2)
        self.assertEqual(t0.nr_of_chapters, 29)
        t1 = vmgi.titles[1]
        self.assertEqual(t1.vts_nr, 1)
        self.assertEqual(t1.nr_of_angles, 2)

    def test_rejects_non_vmg_id(self):
        buf = bytearray(build_vmgi([{"vts_nr": 1}]))
        buf[0:12] = b"NOTAVMGFILE!"
        with self.assertRaises(IFOParseError):
            parse_vmgi(bytes(buf))

    def test_too_small_raises(self):
        with self.assertRaises(IFOParseError):
            parse_vmgi(b"DVDVIDEO-VMG")  # 12 bytes, no header

    def test_title_count_bounded_by_extent(self):
        # nr_titles claims a huge value but last_byte limits the real extent.
        buf = bytearray(build_vmgi([{"vts_nr": 1, "chapters": 5}]))
        base = 1 * SECTOR
        # overwrite nr_of_srpts to a bogus large number; last_byte stays correct
        buf[base : base + 2] = _u16(9999)
        vmgi = parse_vmgi(bytes(buf))
        # extent (last_byte=19) bounds it to exactly 1 entry, not 9999.
        self.assertEqual(len(vmgi.titles), 1)
        self.assertEqual(vmgi.titles[0].nr_of_chapters, 5)


class VtsiTest(unittest.TestCase):
    def test_parse_pgc_duration_exact(self):
        buf = build_vtsi(bytes.fromhex("012125e4"), nr_programs=29, nr_cells=33)
        vtsi = parse_vtsi(buf)
        self.assertTrue(vtsi.is_valid)
        self.assertEqual(vtsi.nr_of_pgcs, 1)
        self.assertEqual(vtsi.nr_of_programs, 29)
        self.assertEqual(vtsi.nr_of_cells, 33)
        self.assertAlmostEqual(
            vtsi.duration_s, 4885 + 24 / (30000 / 1001), places=4
        )

    def test_rejects_non_vts_id(self):
        buf = bytearray(build_vtsi(bytes([0, 0, 0, 0x40])))
        buf[0:12] = b"DVDVIDEO-VMG"  # wrong id for a VTS parse
        with self.assertRaises(IFOParseError):
            parse_vtsi(bytes(buf))

    def test_zero_pgc(self):
        buf = bytearray(build_vtsi(bytes([0, 0, 0, 0x40])))
        # set nr_of_pgci_srp to 0
        pgcit = 2 * SECTOR
        buf[pgcit : pgcit + 2] = _u16(0)
        vtsi = parse_vtsi(bytes(buf))
        self.assertEqual(vtsi.nr_of_pgcs, 0)
        self.assertIsNone(vtsi.duration_s)


if __name__ == "__main__":
    unittest.main()
