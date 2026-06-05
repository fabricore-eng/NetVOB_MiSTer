"""DVD IFO parsing (VMGI TT_SRPT + VTSI PGC) on SYNTHETIC crafted bytes.

Every fixture here is built byte-for-byte in-test (no external assets), so the
assertions pin EXACT structural values. A separate ``test_real_data.py`` runs
the same code on a real (copyrighted, local-only) dump and SKIPS if absent.
"""

import service.tests._bootstrap  # noqa: F401

import struct
import unittest

from service.sources.dvddump.ifo import (
    CellPlayback,
    CellSpan,
    IFOParseError,
    SECTOR,
    decode_dvd_time,
    parse_pgc,
    parse_pgc_for_ttn,
    parse_vmgi,
    parse_vtsi,
    resolve_cell_spans,
    vts_ttn_to_pgcn,
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


def build_pgc(cells, program_map, *, playback_time=b"\x00\x10\x00\x40"):
    """Craft a full PGC: header + offset table + program map + C_PBKIT + C_POSIT.

    ``cells`` is a list of dicts with keys: cat0, cat1, pbtime(4 bytes),
    first_sector, last_vobu_start, last_sector, vob_id, cell_id.
    ``program_map`` is a list of 1-based entry cell numbers.

    Layout (offsets relative to PGC start):
      0x00..0xE3  header (counts at +2/+3, playback_time at +4)
      0xE4        offset table: cmd, program_map, cell_pbk, cell_pos (u16 each)
      0xEC        program map (1 byte per program)
      then        C_PBKIT (24 bytes per cell)
      then        C_POSIT (4 bytes per cell)
    """
    nr_programs = len(program_map)
    nr_cells = len(cells)
    pgc = bytearray(0xEC)
    pgc[2] = nr_programs
    pgc[3] = nr_cells
    pgc[4:8] = playback_time

    program_map_off = 0xEC
    cell_pbk_off = program_map_off + nr_programs
    cell_pos_off = cell_pbk_off + nr_cells * 24

    # offset table at 0xE4 (cmd at 0xE4 left 0; the parser ignores it)
    pgc[0xE4:0xE6] = _u16(0)
    pgc[0xE6:0xE8] = _u16(program_map_off)
    pgc[0xE8:0xEA] = _u16(cell_pbk_off)
    pgc[0xEA:0xEC] = _u16(cell_pos_off)

    # program map
    pgc += bytes(program_map)

    # C_PBKIT
    for c in cells:
        e = bytearray(24)
        e[0] = c.get("cat0", 0x00)
        e[1] = c.get("cat1", 0x00)
        e[4:8] = c.get("pbtime", b"\x00\x00\x05\x40")  # 5 s @ 25fps default
        e[0x08:0x0C] = _u32(c["first_sector"])
        e[0x10:0x14] = _u32(c.get("last_vobu_start", c["last_sector"]))
        e[0x14:0x18] = _u32(c["last_sector"])
        pgc += e

    # C_POSIT
    for c in cells:
        e = bytearray(4)
        e[0:2] = _u16(c.get("vob_id", 1))
        e[3] = c.get("cell_id", 1)
        pgc += e

    return bytes(pgc)


def build_vtsi_full(pgc_bytes, *, pgcit_sector=2):
    """Craft a VTSI with one PGCI_SRP pointing at ``pgc_bytes`` (a full PGC)."""
    mat = bytearray(SECTOR)
    mat[0:12] = b"DVDVIDEO-VTS"
    mat[0xCC:0xD0] = _u32(pgcit_sector)

    nr_pgci = 1
    pgc_start_byte = 8 + nr_pgci * 8
    pgcit = bytearray()
    pgcit += _u16(nr_pgci)
    pgcit += b"\x00\x00"
    pgcit += _u32(0)
    srp = bytearray(8)
    srp[0] = 0x81
    srp[4:8] = _u32(pgc_start_byte)
    pgcit += srp
    pgcit += pgc_bytes

    buf = bytearray(mat)
    base = pgcit_sector * SECTOR
    if len(buf) < base + len(pgcit):
        buf += bytes(base + len(pgcit) - len(buf))
    buf[base : base + len(pgcit)] = pgcit
    return bytes(buf)


def build_vts_ptt_srpt(ttn_to_pgcn):
    """Craft a VTS_PTT_SRPT mapping each 1-based vts_ttn to a PGC number.

    ``ttn_to_pgcn`` is a list where entry i (0-based) is the PGC number for
    vts_ttn i+1. Layout matches :func:`ifo.vts_ttn_to_pgcn`:
      +0x00 u16 nr_of_srpts, +0x02 u16 zero, +0x04 u32 last_byte,
      +0x08 u32 ttu_offset[nr] (from table start) -> ptt_info{u16 pgcn, u16 pgn}.
    """
    nr = len(ttn_to_pgcn)
    header_len = 8 + 4 * nr
    body = bytearray()
    ttu_offsets = []
    for pgcn in ttn_to_pgcn:
        ttu_offsets.append(header_len + len(body))
        body += _u16(pgcn) + _u16(1)  # first PTT: pgcn, pgn=1
    out = bytearray()
    out += _u16(nr) + b"\x00\x00" + _u32(header_len + len(body))
    for off in ttu_offsets:
        out += _u32(off)
    out += body
    return bytes(out)


def build_vtsi_multi(pgc_list, ttn_to_pgcn, *, pgcit_sector=2, ptt_srpt_sector=1):
    """Craft a VTSI with MULTIPLE PGCs + a VTS_PTT_SRPT (multi-title VTS).

    ``pgc_list`` is a list of full PGC byte blobs (from :func:`build_pgc`);
    ``ttn_to_pgcn`` maps each title to its 1-based PGC number.
    """
    mat = bytearray(SECTOR)
    mat[0:12] = b"DVDVIDEO-VTS"
    mat[0xC8:0xCC] = _u32(ptt_srpt_sector)
    mat[0xCC:0xD0] = _u32(pgcit_sector)

    nr_pgci = len(pgc_list)
    srp_table_len = nr_pgci * 8
    pgc_blob = bytearray()
    starts = []
    for pgc_bytes in pgc_list:
        starts.append(8 + srp_table_len + len(pgc_blob))  # from PGCIT start
        pgc_blob += pgc_bytes
    pgcit = bytearray()
    pgcit += _u16(nr_pgci) + b"\x00\x00" + _u32(0)
    for i in range(nr_pgci):
        srp = bytearray(8)
        srp[0] = 0x81 if i == 0 else 0x80
        srp[4:8] = _u32(starts[i])
        pgcit += srp
    pgcit += pgc_blob

    ptt = build_vts_ptt_srpt(ttn_to_pgcn)

    buf = bytearray(mat)

    def _place(sector, blob):
        base = sector * SECTOR
        if len(buf) < base + len(blob):
            buf.extend(bytes(base + len(blob) - len(buf)))
        buf[base : base + len(blob)] = blob

    _place(ptt_srpt_sector, ptt)
    _place(pgcit_sector, pgcit)
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

    def test_ptt_srpt_selects_correct_pgc_per_title(self):
        # Two titles in ONE VTS: vts_ttn 1 -> PGC 1, vts_ttn 2 -> PGC 2. Each PGC
        # has a distinguishable cell (different first_sector). Verifies the
        # PTT_SRPT mapping resolves each title to ITS OWN PGC, not always PGC 1.
        pgc1 = build_pgc(
            [{"first_sector": 100, "last_sector": 199, "cell_id": 1}],
            program_map=[1],
        )
        pgc2 = build_pgc(
            [{"first_sector": 5000, "last_sector": 5099, "cell_id": 1}],
            program_map=[1],
        )
        buf = build_vtsi_multi([pgc1, pgc2], ttn_to_pgcn=[1, 2])

        # PTT_SRPT mapping itself.
        self.assertEqual(vts_ttn_to_pgcn(buf), [1, 2])

        # Title 1 resolves to PGC 1 (cell at sector 100).
        p1 = parse_pgc_for_ttn(buf, 1)
        self.assertEqual(p1.pgc_nr, 1)
        self.assertEqual(p1.cells[0].first_sector, 100)

        # Title 2 resolves to PGC 2 (cell at sector 5000) — NOT PGC 1.
        p2 = parse_pgc_for_ttn(buf, 2)
        self.assertEqual(p2.pgc_nr, 2)
        self.assertEqual(p2.cells[0].first_sector, 5000)

    def test_pgc_for_ttn_no_ptt_srpt_falls_back_to_ttn(self):
        # A VTS with no PTT_SRPT (sector 0): ttn 1 must still resolve to PGC 1.
        buf = build_vtsi_full(
            build_pgc(
                [{"first_sector": 7, "last_sector": 9, "cell_id": 1}],
                program_map=[1],
            )
        )
        self.assertEqual(vts_ttn_to_pgcn(buf), [])  # no PTT_SRPT
        p = parse_pgc_for_ttn(buf, 1)
        self.assertEqual(p.pgc_nr, 1)
        self.assertEqual(p.cells[0].first_sector, 7)


class PgcCellOrderTest(unittest.TestCase):
    """Craft a PGC with a known program map + cell table; assert EXACT order."""

    def _three_cell_pgc(self):
        # 3 contiguous cells, programs entering cells 1 and 3 (program 2 starts
        # mid-stream at cell 3, so cell 2 is a continuation of program 1).
        cells = [
            {"first_sector": 0, "last_sector": 9, "vob_id": 1, "cell_id": 1,
             "cat0": 0x02},
            {"first_sector": 10, "last_sector": 24, "vob_id": 1, "cell_id": 2,
             "cat0": 0x08},
            {"first_sector": 25, "last_sector": 39, "vob_id": 2, "cell_id": 1,
             "cat0": 0x0A},
        ]
        return build_pgc(cells, program_map=[1, 3])

    def test_parse_pgc_program_map_and_cell_order_exact(self):
        pgc_bytes = self._three_cell_pgc()
        # Parse it standalone at offset 0.
        pgc = parse_pgc(pgc_bytes, 0, pgc_nr=1)
        self.assertEqual(pgc.nr_of_programs, 2)
        self.assertEqual(pgc.nr_of_cells, 3)
        self.assertEqual(pgc.program_map, [1, 3])
        # Cells come back in table order == PGC playback order.
        self.assertEqual([c.cell_nr for c in pgc.cells], [1, 2, 3])
        self.assertEqual(
            [(c.first_sector, c.last_sector) for c in pgc.cells],
            [(0, 9), (10, 24), (25, 39)],
        )
        # vob_id/cell_id attached from the cell position table.
        self.assertEqual(
            [(c.vob_id, c.cell_id) for c in pgc.cells],
            [(1, 1), (1, 2), (2, 1)],
        )
        # category-byte bitfields decode (cell 3 cat0=0x0A => interleaved bit).
        self.assertEqual(pgc.cells[0].category0, 0x02)
        self.assertTrue(pgc.cells[2].interleaved)  # 0x0A bit1 set
        self.assertFalse(pgc.cells[1].interleaved)  # 0x08 bit1 clear

    def test_inverted_cell_rejected(self):
        # Corrupt/truncated IFO (scratched/aged dump): a cell with
        # last_sector < first_sector must be rejected at parse, not silently
        # yield zero spans + a negative nr_sectors that moves offsets backward.
        cells = [
            {"first_sector": 0, "last_sector": 9, "vob_id": 1, "cell_id": 1,
             "cat0": 0x02},
            {"first_sector": 1000, "last_sector": 500, "vob_id": 1, "cell_id": 2,
             "cat0": 0x02},  # INVERTED: last < first
        ]
        pgc_bytes = build_pgc(cells, program_map=[1])
        with self.assertRaises(IFOParseError):
            parse_pgc(pgc_bytes, 0, pgc_nr=1)

    def test_nr_sectors_clamped_nonnegative(self):
        # Defense-in-depth: nr_sectors never goes negative even for an inverted cell.
        c = CellPlayback(
            cell_nr=1, category0=0, category1=0, playback_time_s=None,
            first_sector=1000, last_vobu_start_sector=500, last_sector=500,
        )
        self.assertEqual(c.nr_sectors, 0)

    def test_parse_vtsi_full_exposes_pgc_cells(self):
        buf = build_vtsi_full(self._three_cell_pgc())
        vtsi = parse_vtsi(buf)
        self.assertTrue(vtsi.is_valid)
        self.assertEqual(vtsi.nr_of_pgcs, 1)
        self.assertEqual(vtsi.nr_of_programs, 2)
        self.assertEqual(vtsi.nr_of_cells, 3)
        self.assertIsNotNone(vtsi.pgc)
        self.assertEqual([c.cell_nr for c in vtsi.pgc.cells], [1, 2, 3])

    def test_minimal_pgc_without_offset_table_degrades(self):
        # A PGC truncated to its first 8 bytes (no offset table) parses to
        # counts/duration with empty maps rather than raising.
        pgc = parse_pgc(b"\x00\x00\x05\x07" + b"\x00\x10\x00\x40", 0, pgc_nr=1)
        self.assertEqual(pgc.nr_of_programs, 5)
        self.assertEqual(pgc.nr_of_cells, 7)
        self.assertEqual(pgc.program_map, [])
        self.assertEqual(pgc.cells, [])


class ResolveCellSpansTest(unittest.TestCase):
    """Map cell sector ranges to per-file byte spans (synthetic, no real VOBs)."""

    def test_single_part_one_span_per_cell(self):
        pgc = parse_pgc(
            build_pgc(
                [
                    {"first_sector": 0, "last_sector": 9, "cell_id": 1},
                    {"first_sector": 10, "last_sector": 19, "cell_id": 2},
                ],
                program_map=[1, 2],
            ),
            0,
            1,
        )
        parts = ["/x/VTS_01_1.VOB"]
        sizes = [20 * SECTOR]  # one part holds all 20 sectors
        spans = resolve_cell_spans(pgc.cells, parts, part_sizes=sizes)
        self.assertEqual(len(spans), 2)
        self.assertEqual(
            [(s.vob_file, s.start, s.end, s.cell_nr) for s in spans],
            [
                ("/x/VTS_01_1.VOB", 0, 10 * SECTOR, 1),
                ("/x/VTS_01_1.VOB", 10 * SECTOR, 20 * SECTOR, 2),
            ],
        )

    def test_cell_straddling_part_boundary_splits(self):
        # One cell [0..19]; part 0 is 12 sectors, so the cell splits 12 / 8.
        pgc = parse_pgc(
            build_pgc(
                [{"first_sector": 0, "last_sector": 19, "cell_id": 1}],
                program_map=[1],
            ),
            0,
            1,
        )
        parts = ["/x/VTS_01_1.VOB", "/x/VTS_01_2.VOB"]
        sizes = [12 * SECTOR, 8 * SECTOR]
        spans = resolve_cell_spans(pgc.cells, parts, part_sizes=sizes)
        self.assertEqual(len(spans), 2)
        self.assertEqual(
            spans[0], CellSpan("/x/VTS_01_1.VOB", 0, 12 * SECTOR, 1)
        )
        self.assertEqual(
            spans[1], CellSpan("/x/VTS_01_2.VOB", 0, 8 * SECTOR, 1)
        )
        # The two spans reconstruct the cell's full byte length.
        self.assertEqual(sum(s.length for s in spans), 20 * SECTOR)

    def test_cell_past_available_vob_set_raises(self):
        pgc = parse_pgc(
            build_pgc(
                [{"first_sector": 0, "last_sector": 99, "cell_id": 1}],
                program_map=[1],
            ),
            0,
            1,
        )
        # Only 10 sectors available but the cell needs 100 (truncated dump).
        with self.assertRaises(IFOParseError):
            resolve_cell_spans(
                pgc.cells, ["/x/VTS_01_1.VOB"], part_sizes=[10 * SECTOR]
            )

    def test_empty_vob_set_raises(self):
        with self.assertRaises(IFOParseError):
            resolve_cell_spans([], [])


if __name__ == "__main__":
    unittest.main()
