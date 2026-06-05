"""Pure-Python DVD IFO parser — enough to enumerate titles for ``browse()``.

This replaces the ``browse()`` stub's "list ``*.VOB`` files" behavior with real
**title enumeration** read from the DVD's own navigation metadata, with **zero
third-party dependencies** (no ``libdvdread``). It parses:

* **VMGI** (``VIDEO_TS.IFO``): the id string, spec version, the number of title
  sets, and the **TT_SRPT** (Title Search Pointer Table) that lists every
  *title* on the disc and which **VTS** + title-within-VTS each maps to, plus
  its chapter (PTT) and angle counts.
* **VTSI** (``VTS_nn_0.IFO``): the id string and the **VTS_PGCIT** (Program
  Chain Information Table), from which we read the first PGC's **playback time**
  (the title's duration) and program/cell counts.

Scope (per the cycle brief): enumerate title sets + identify the main feature,
with durations where the PGC is reachable. Deeper PGC walking (per-chapter time
maps, multi-PGC menus, cell command parsing) is left as a TODO; this is the
verifiable-now metadata layer that makes ``browse()`` return real titles.

------------------------------------------------------------------------------
On-disc layout (ISO/IEC 16448 + the de-facto DVD-Video spec, as encoded by
``dvdread``'s ``ifo_types.h``). All multi-byte integers are **big-endian**.
------------------------------------------------------------------------------
VMGI mat (``VIDEO_TS.IFO``):
  0x00  char[12]  identifier        "DVDVIDEO-VMG"
  0x1C  u32       vmgi_last_sector
  0x20  u8        specification_version (high nibble = major, low = minor)
  0x3E  u16       vmg_nr_of_title_sets
  0xC4  u32       vmgi_tt_srpt sector (relative to start of VMGI)

TT_SRPT (at ``tt_srpt_sector * 2048``):
  +0x00 u16  nr_of_srpts (number of titles)
  +0x04 u32  last_byte
  +0x08 title_info[ ] — 12 bytes each:
        +0x00 u8   playback_type
        +0x01 u8   nr_of_angles
        +0x02 u16  nr_of_ptts (chapters)
        +0x04 u16  parental_id
        +0x06 u8   title_set_nr (VTS number, 1-based)
        +0x07 u8   vts_ttn (title number within the VTS)
        +0x08 u32  title_set_sector (start sector of the VTS)

VTSI mat (``VTS_nn_0.IFO``):
  0x00  char[12]  identifier        "DVDVIDEO-VTS"
  0x1C  u32       vtsi_last_sector
  0xC8  u32       vts_ptt_srpt sector
  0xCC  u32       vts_pgcit    sector  (relative to start of VTSI)

VTS_PGCIT (at ``vts_pgcit_sector * 2048``):
  +0x00 u16  nr_of_pgci_srp
  +0x04 u32  last_byte
  +0x08 pgci_srp[ ] — 8 bytes each:
        +0x00 u8   entry_id
        +0x04 u32  pgc_start_byte (relative to start of VTS_PGCIT)

PGC (at ``pgcit_byte + pgc_start_byte``), the ``pgc_t`` of ``dvdread``:
  +0x02 u8   nr_of_programs
  +0x03 u8   nr_of_cells
  +0x04 u32  playback_time (dvd_time_t: BCD hour, min, sec, frame; the frame
             byte's top 2 bits select the frame rate: 0b01=25, 0b11=29.97)
  +0xE4 u16  pgc_command_tbl_offset (relative to start of PGC)
  +0xE6 u16  program_map_offset     (relative to start of PGC)
  +0xE8 u16  cell_playback_offset   (relative to start of PGC)
  +0xEA u16  cell_position_offset   (relative to start of PGC)

Program map (at ``pgc + program_map_offset``):
  nr_of_programs bytes; each byte = the 1-based cell number that program enters.

Cell playback info table / C_PBKIT (at ``pgc + cell_playback_offset``),
  ``cell_playback_t`` — 24 bytes each, ``nr_of_cells`` entries:
  +0x00 u8   category byte 0: block_mode(2) | block_type(2) | seamless_play(1)
             | interleaved(1) | stc_discontinuity(1) | seamless_angle(1)
  +0x01 u8   category byte 1: playback flags (restricted, cell_type, ...)
  +0x02 u8   still_time
  +0x03 u8   cell_cmd_nr
  +0x04 u32  cell playback_time (dvd_time_t)
  +0x08 u32  first_sector            (VOBU start sector of the cell)
  +0x0C u32  first_ilvu_end_sector
  +0x10 u32  last_vobu_start_sector
  +0x14 u32  last_sector             (last sector of the cell, inclusive)
  Cell sectors are RELATIVE to the start of the VTS title VOB *set* — the
  concatenation of ``VTS_nn_1.VOB`` .. ``VTS_nn_9.VOB`` (the menu VOB
  ``VTS_nn_0.VOB`` is NOT part of this address space).

Cell position info table (at ``pgc + cell_position_offset``), ``cell_position_t``
  — 4 bytes each, ``nr_of_cells`` entries:
  +0x00 u16  vob_id (VOB identifier the cell lives in)
  +0x03 u8   cell_id
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass, field
from typing import Optional

SECTOR = 2048

VMG_ID = b"DVDVIDEO-VMG"
VTS_ID = b"DVDVIDEO-VTS"


class IFOParseError(ValueError):
    """Malformed or unexpected IFO structure."""


def _u8(b: bytes, o: int) -> int:
    if o + 1 > len(b):
        raise IFOParseError(f"u8 read past end at {o}")
    return b[o]


def _u16(b: bytes, o: int) -> int:
    if o + 2 > len(b):
        raise IFOParseError(f"u16 read past end at {o}")
    return struct.unpack(">H", b[o : o + 2])[0]


def _u32(b: bytes, o: int) -> int:
    if o + 4 > len(b):
        raise IFOParseError(f"u32 read past end at {o}")
    return struct.unpack(">I", b[o : o + 4])[0]


def _bcd(x: int) -> int:
    return (x >> 4) * 10 + (x & 0x0F)


# Frame-rate code in the top 2 bits of the dvd_time_t frame byte.
_FPS_BY_CODE = {0b01: 25.0, 0b11: 30000.0 / 1001.0}


def decode_dvd_time(pt: bytes) -> tuple[Optional[float], Optional[float]]:
    """Decode a 4-byte ``dvd_time_t`` to ``(seconds, fps)``.

    Returns ``(None, None)`` if the frame-rate code is the "not specified"
    value (0b00 / 0b10), which we cannot turn into a precise duration.
    """
    if len(pt) < 4:
        raise IFOParseError("dvd_time needs 4 bytes")
    hour = _bcd(pt[0])
    minute = _bcd(pt[1])
    second = _bcd(pt[2])
    fps_code = (pt[3] >> 6) & 0x3
    frames = _bcd(pt[3] & 0x3F)
    fps = _FPS_BY_CODE.get(fps_code)
    if fps is None:
        return None, None
    total = hour * 3600 + minute * 60 + second + frames / fps
    return total, fps


@dataclass
class TitleInfo:
    """One title from the VMG TT_SRPT (a playable title, not a raw file)."""

    title_nr: int  # 1-based global title number
    vts_nr: int  # which VTS (1-based) holds this title's VOBs
    vts_ttn: int  # title number within that VTS
    nr_of_chapters: int
    nr_of_angles: int
    title_set_sector: int
    playback_type: int


@dataclass
class VMGI:
    """Parsed Video Manager information (``VIDEO_TS.IFO``)."""

    identifier: bytes
    spec_version: tuple[int, int]
    nr_of_title_sets: int
    titles: list[TitleInfo] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.identifier == VMG_ID


@dataclass
class CellPlayback:
    """One cell from a PGC's cell playback info table (``C_PBKIT``).

    Sectors are relative to the start of the VTS title VOB *set* (the
    concatenation of ``VTS_nn_1.VOB`` .. ``VTS_nn_9.VOB``). ``first_sector`` and
    ``last_sector`` are inclusive, so the cell occupies sectors
    ``[first_sector, last_sector]`` => byte range
    ``[first_sector*2048, (last_sector+1)*2048)`` in that address space.
    """

    cell_nr: int  # 1-based index within the PGC's cell table
    category0: int  # category byte 0 (block_mode/block_type/seamless/...)
    category1: int  # category byte 1 (playback flags)
    playback_time_s: Optional[float]
    first_sector: int
    last_vobu_start_sector: int
    last_sector: int
    vob_id: Optional[int] = None  # from the cell position info table
    cell_id: Optional[int] = None

    @property
    def nr_sectors(self) -> int:
        """Inclusive sector count of the cell (clamped >= 0 as defense against a
        corrupt/inverted IFO cell where last_sector < first_sector)."""
        return max(0, self.last_sector - self.first_sector + 1)

    # Category byte 0 bitfields (per dvdread cell_playback_t).
    @property
    def block_mode(self) -> int:
        return (self.category0 >> 6) & 0x3

    @property
    def block_type(self) -> int:
        return (self.category0 >> 4) & 0x3

    @property
    def interleaved(self) -> bool:
        return bool((self.category0 >> 1) & 0x1)


@dataclass
class PGC:
    """A parsed Program Chain — programs over an ordered list of cells."""

    pgc_nr: int  # 1-based index of this PGC in the VTS_PGCIT
    nr_of_programs: int
    nr_of_cells: int
    duration_s: Optional[float] = None
    fps: Optional[float] = None
    # program_map[i] = 1-based cell number that program (i+1) enters.
    program_map: list[int] = field(default_factory=list)
    # Cells in PGC playback order (== table order for a linear, single-angle PGC).
    cells: list[CellPlayback] = field(default_factory=list)


@dataclass
class VTSI:
    """Parsed Video Title Set information (``VTS_nn_0.IFO``)."""

    identifier: bytes
    nr_of_pgcs: int
    # First PGC summary (the title feature PGC for a one-PGC title set):
    duration_s: Optional[float] = None
    fps: Optional[float] = None
    nr_of_programs: Optional[int] = None
    nr_of_cells: Optional[int] = None
    # Fully parsed first PGC (program map + cell playback table), when reachable.
    pgc: Optional[PGC] = None

    @property
    def is_valid(self) -> bool:
        return self.identifier == VTS_ID


def parse_vmgi(buf: bytes) -> VMGI:
    """Parse a ``VIDEO_TS.IFO`` byte buffer into a :class:`VMGI`."""
    if len(buf) < 0xC8:
        raise IFOParseError(f"VMGI too small: {len(buf)} bytes")
    ident = bytes(buf[0:12])
    if ident != VMG_ID:
        raise IFOParseError(f"not a VMG IFO: id={ident!r}")
    ver = _u8(buf, 0x20)
    spec = (ver >> 4, ver & 0x0F)
    nr_title_sets = _u16(buf, 0x3E)

    vmgi = VMGI(identifier=ident, spec_version=spec, nr_of_title_sets=nr_title_sets)

    tt_srpt_sector = _u32(buf, 0xC4)
    if tt_srpt_sector == 0:
        return vmgi  # no title table (shouldn't happen on a real disc)
    base = tt_srpt_sector * SECTOR
    if base + 8 > len(buf):
        raise IFOParseError(
            f"TT_SRPT sector {tt_srpt_sector} (byte {base}) past end "
            f"({len(buf)} bytes)"
        )
    nr_titles = _u16(buf, base)
    last_byte = _u32(buf, base + 4)
    # Defensive bound: don't trust nr_titles past the table's declared extent.
    max_by_extent = (last_byte + 1 - 8) // 12 if last_byte >= 7 else 0
    n = min(nr_titles, max(max_by_extent, 0))
    for i in range(n):
        e = base + 8 + i * 12
        if e + 12 > len(buf):
            raise IFOParseError(f"TT_SRPT entry {i} runs past end at {e}")
        vmgi.titles.append(
            TitleInfo(
                title_nr=i + 1,
                playback_type=_u8(buf, e + 0),
                nr_of_angles=_u8(buf, e + 1),
                nr_of_chapters=_u16(buf, e + 2),
                vts_nr=_u8(buf, e + 6),
                vts_ttn=_u8(buf, e + 7),
                title_set_sector=_u32(buf, e + 8),
            )
        )
    return vmgi


def parse_pgc(buf: bytes, pgc: int, pgc_nr: int) -> PGC:
    """Parse one PGC at absolute byte offset ``pgc`` into a :class:`PGC`.

    Reads the program map (program -> entry cell) and the cell playback info
    table (``C_PBKIT``: per-cell category + first/last sector), plus the cell
    position info table (vob_id/cell_id) when present. The cell list is in PGC
    playback order, which for a linear single-angle PGC equals table order.
    """
    # The basic PGC header (counts + playback time) needs 8 bytes; the offset
    # table that points at the program map / cell tables lives at +0xE4..+0xEC.
    # A real disc always has it; minimal/synthetic PGCs may stop at +8, in which
    # case we return counts/duration with empty maps rather than erroring.
    if pgc + 8 > len(buf):
        raise IFOParseError(f"PGC at byte {pgc} past end ({len(buf)} bytes)")
    nr_programs = _u8(buf, pgc + 2)
    nr_cells = _u8(buf, pgc + 3)
    duration_s, fps = decode_dvd_time(buf[pgc + 4 : pgc + 8])

    out = PGC(
        pgc_nr=pgc_nr,
        nr_of_programs=nr_programs,
        nr_of_cells=nr_cells,
        duration_s=duration_s,
        fps=fps,
    )

    if pgc + 0xEC > len(buf):
        return out  # no offset table available; counts/duration only

    program_map_off = _u16(buf, pgc + 0xE6)
    cell_pbk_off = _u16(buf, pgc + 0xE8)
    cell_pos_off = _u16(buf, pgc + 0xEA)

    # Program map: nr_programs bytes, each the 1-based entry cell number.
    if program_map_off:
        pmap = pgc + program_map_off
        if pmap + nr_programs > len(buf):
            raise IFOParseError(f"program map at {pmap} past end")
        out.program_map = [_u8(buf, pmap + i) for i in range(nr_programs)]

    # Cell position info table (vob_id/cell_id), parsed first so we can attach
    # it to each cell below. 4 bytes per cell.
    positions: list[tuple[int, int]] = []
    if cell_pos_off:
        cpos = pgc + cell_pos_off
        if cpos + nr_cells * 4 > len(buf):
            raise IFOParseError(f"cell position table at {cpos} past end")
        for i in range(nr_cells):
            e = cpos + i * 4
            positions.append((_u16(buf, e), _u8(buf, e + 3)))

    # Cell playback info table (C_PBKIT): 24 bytes per cell.
    if cell_pbk_off:
        cpbk = pgc + cell_pbk_off
        if cpbk + nr_cells * 24 > len(buf):
            raise IFOParseError(f"cell playback table at {cpbk} past end")
        for i in range(nr_cells):
            e = cpbk + i * 24
            ptime_s, _ = decode_dvd_time(buf[e + 4 : e + 8])
            vob_id = positions[i][0] if i < len(positions) else None
            cell_id = positions[i][1] if i < len(positions) else None
            first_sector = _u32(buf, e + 0x08)
            last_sector = _u32(buf, e + 0x14)
            if last_sector < first_sector:
                # Corrupt/truncated IFO (e.g. a scratched/aged dump): an inverted
                # cell range would otherwise yield zero spans + a NEGATIVE sector
                # count downstream (moving read offsets backward -> mis-routing).
                # Surface it loudly, matching the parser's other malformation checks.
                raise IFOParseError(
                    f"cell {i + 1}: inverted sector range "
                    f"first={first_sector} > last={last_sector}"
                )
            out.cells.append(
                CellPlayback(
                    cell_nr=i + 1,
                    category0=_u8(buf, e + 0),
                    category1=_u8(buf, e + 1),
                    playback_time_s=ptime_s,
                    first_sector=first_sector,
                    last_vobu_start_sector=_u32(buf, e + 0x10),
                    last_sector=last_sector,
                    vob_id=vob_id,
                    cell_id=cell_id,
                )
            )
    return out


def parse_vtsi(buf: bytes) -> VTSI:
    """Parse a ``VTS_nn_0.IFO`` byte buffer into a :class:`VTSI`.

    Reads the first PGC's playback time / program / cell counts AND fully parses
    that PGC's program map + cell playback table (see :func:`parse_pgc`), which
    drives ordered cell streaming in ``open()``. Multi-PGC title sets expose
    only their first PGC here (TODO: walk all PGCI_SRP entries); the first PGC is
    the feature PGC for the common one-title-per-VTS layout this targets.
    """
    if len(buf) < 0xD0:
        raise IFOParseError(f"VTSI too small: {len(buf)} bytes")
    ident = bytes(buf[0:12])
    if ident != VTS_ID:
        raise IFOParseError(f"not a VTS IFO: id={ident!r}")

    vts_pgcit_sector = _u32(buf, 0xCC)
    if vts_pgcit_sector == 0:
        return VTSI(identifier=ident, nr_of_pgcs=0)

    pgcit = vts_pgcit_sector * SECTOR
    if pgcit + 8 > len(buf):
        raise IFOParseError(
            f"VTS_PGCIT sector {vts_pgcit_sector} (byte {pgcit}) past end "
            f"({len(buf)} bytes)"
        )
    nr_pgci = _u16(buf, pgcit)
    out = VTSI(identifier=ident, nr_of_pgcs=nr_pgci)
    if nr_pgci == 0:
        return out

    # First PGCI_SRP entry -> its PGC.
    srp = pgcit + 8
    pgc_start_byte = _u32(buf, srp + 4)
    pgc_byte = pgcit + pgc_start_byte
    pgc = parse_pgc(buf, pgc_byte, pgc_nr=1)
    out.pgc = pgc
    out.nr_of_programs = pgc.nr_of_programs
    out.nr_of_cells = pgc.nr_of_cells
    out.duration_s = pgc.duration_s
    out.fps = pgc.fps
    return out


def vts_ttn_to_pgcn(buf: bytes) -> list[int]:
    """Map each *title within this VTS* (1-based vts_ttn) to its PGC number.

    Reads ``VTS_PTT_SRPT`` (offset 0xC8 -> sector). Layout (``vts_ptt_srpt_t``
    in dvdread), all relative to the table's start byte:
      +0x00 u16  nr_of_srpts (titles within this VTS)
      +0x04 u32  last_byte
      +0x08 u32  ttu_offset[nr_of_srpts] — byte offset (from table start) to
                 each title's PTT array (``ptt_info_t[]``, 4 bytes each:
                 +0x00 u16 pgcn, +0x02 u16 pgn). The title's FIRST PTT gives the
                 PGC that plays the whole title.
    Returns ``[pgcn_for_ttn1, pgcn_for_ttn2, ...]``. Empty list if there is no
    PTT_SRPT (one-PGC-per-VTS discs that omit it) — callers then default ttn->ttn.
    """
    ptt_srpt_sector = _u32(buf, 0xC8)
    if ptt_srpt_sector == 0:
        return []
    base = ptt_srpt_sector * SECTOR
    if base + 8 > len(buf):
        raise IFOParseError(
            f"VTS_PTT_SRPT sector {ptt_srpt_sector} (byte {base}) past end "
            f"({len(buf)} bytes)"
        )
    nr_srpts = _u16(buf, base)
    pgcns: list[int] = []
    for i in range(nr_srpts):
        off_pos = base + 8 + 4 * i
        if off_pos + 4 > len(buf):
            raise IFOParseError("VTS_PTT_SRPT ttu_offset table truncated")
        ttu_off = _u32(buf, off_pos)
        if base + ttu_off + 2 > len(buf):
            raise IFOParseError("VTS_PTT_SRPT ptt entry past end")
        pgcns.append(_u16(buf, base + ttu_off))
    return pgcns


def parse_pgc_for_ttn(buf: bytes, vts_ttn: int) -> PGC:
    """Parse the PGC that plays *title-within-VTS* ``vts_ttn`` (1-based).

    Resolves vts_ttn -> PGCN via :func:`vts_ttn_to_pgcn` (VTS_PTT_SRPT), then
    fetches that PGC's PGCI_SRP entry from VTS_PGCIT and parses it. Fixes the
    "two titles in one VTS both play the first PGC" bug. Falls back to
    ``pgcn == vts_ttn`` (clamped) when no PTT_SRPT is present, so a single-title
    VTS (ttn 1) still resolves to PGC 1.
    """
    if vts_ttn < 1:
        raise IFOParseError(f"vts_ttn must be >= 1, got {vts_ttn}")
    if len(buf) < 0xD0:
        raise IFOParseError(f"VTSI too small: {len(buf)} bytes")

    pgcns = vts_ttn_to_pgcn(buf)
    if pgcns:
        if vts_ttn > len(pgcns):
            raise IFOParseError(
                f"vts_ttn {vts_ttn} > nr_of_srpts {len(pgcns)}"
            )
        pgcn = pgcns[vts_ttn - 1]
    else:
        pgcn = vts_ttn  # no PTT_SRPT: title N -> PGC N (clamped below)

    vts_pgcit_sector = _u32(buf, 0xCC)
    if vts_pgcit_sector == 0:
        raise IFOParseError("VTSI has no VTS_PGCIT")
    pgcit = vts_pgcit_sector * SECTOR
    if pgcit + 8 > len(buf):
        raise IFOParseError(
            f"VTS_PGCIT sector {vts_pgcit_sector} past end ({len(buf)} bytes)"
        )
    nr_pgci = _u16(buf, pgcit)
    if nr_pgci == 0:
        raise IFOParseError("VTS_PGCIT has no PGCs")
    if pgcn < 1 or pgcn > nr_pgci:
        # Out-of-range PGCN (corrupt PTT or the ttn-fallback overshooting a
        # single-PGC VTS): clamp to the last real PGC rather than fail.
        pgcn = min(max(pgcn, 1), nr_pgci)

    srp = pgcit + 8 + (pgcn - 1) * 8
    if srp + 8 > len(buf):
        raise IFOParseError(f"PGCI_SRP[{pgcn}] past end")
    pgc_start_byte = _u32(buf, srp + 4)
    return parse_pgc(buf, pgcit + pgc_start_byte, pgc_nr=pgcn)


# A DVD title VOB file (VTS_nn_1.VOB ..) is capped at 1 GB. The cell address
# space is the byte-concatenation of those files in order; a global sector maps
# into a specific file by integer division, EXCEPT the cap is in *bytes* not a
# fixed sector count — so we resolve against the actual on-disk file sizes.
ONE_GB = 1024 * 1024 * 1024
MAX_VOB_PART = 9  # VTS_nn_1.VOB .. VTS_nn_9.VOB


@dataclass
class CellSpan:
    """A contiguous byte range of one VOB file to stream for a cell.

    A cell can straddle a 1 GB VOB-file boundary, so one cell may yield more
    than one :class:`CellSpan` (one per file it touches). ``start``/``end`` are
    byte offsets within ``vob_file`` with ``end`` exclusive.
    """

    vob_file: str  # absolute path to the VTS_nn_k.VOB
    start: int  # byte offset within the file (inclusive)
    end: int  # byte offset within the file (exclusive)
    cell_nr: int  # 1-based PGC cell number this span belongs to

    @property
    def length(self) -> int:
        return self.end - self.start


def vts_vob_parts(video_ts_dir: str, vts_nr: int) -> list[str]:
    """Return existing ``VTS_nn_1.VOB`` .. ``VTS_nn_9.VOB`` paths, in order.

    Handles upper/lower-case naming. Stops at the first missing part number so
    the returned list is the contiguous VOB set that forms the cell address
    space. Returns ``[]`` if no title VOB is present.
    """
    parts: list[str] = []
    for k in range(1, MAX_VOB_PART + 1):
        found = None
        for nm in (f"VTS_{vts_nr:02d}_{k}.VOB", f"vts_{vts_nr:02d}_{k}.vob"):
            p = os.path.join(video_ts_dir, nm)
            if os.path.isfile(p):
                found = p
                break
        if found is None:
            break
        parts.append(found)
    return parts


def resolve_cell_spans(
    cells: list["CellPlayback"],
    vob_parts: list[str],
    *,
    part_sizes: Optional[list[int]] = None,
) -> list[CellSpan]:
    """Map PGC cells (VTS-relative sectors) to per-file byte spans, in order.

    ``vob_parts`` is the ordered VTS title VOB set (see :func:`vts_vob_parts`).
    The cell address space is the byte-concatenation of those files. A cell that
    crosses a file boundary is split into one :class:`CellSpan` per file.

    ``part_sizes`` overrides the on-disk file sizes (used by synthetic tests so
    no real files are needed); otherwise sizes are read via ``os.path.getsize``.
    Raises :class:`IFOParseError` if a cell's sectors fall outside the available
    VOB set (e.g. a truncated local *slice* of the dump).
    """
    if not vob_parts:
        raise IFOParseError("no VTS title VOB files to resolve cells against")
    if part_sizes is None:
        part_sizes = [os.path.getsize(p) for p in vob_parts]
    if len(part_sizes) != len(vob_parts):
        raise IFOParseError("part_sizes length != vob_parts length")

    # Cumulative byte offset at which each part begins in the address space.
    starts: list[int] = []
    acc = 0
    for sz in part_sizes:
        starts.append(acc)
        acc += sz
    total = acc

    spans: list[CellSpan] = []
    for cell in cells:
        cell_begin = cell.first_sector * SECTOR
        cell_end = (cell.last_sector + 1) * SECTOR  # exclusive
        if cell_begin >= total or cell_end > total:
            raise IFOParseError(
                f"cell {cell.cell_nr} bytes [{cell_begin},{cell_end}) exceed "
                f"VOB set size {total} (truncated dump?)"
            )
        # Walk the cell across however many parts it spans.
        pos = cell_begin
        for idx, p in enumerate(vob_parts):
            p_begin = starts[idx]
            p_end = starts[idx] + part_sizes[idx]
            if pos >= cell_end:
                break
            if pos >= p_end or cell_end <= p_begin:
                continue
            seg_begin = max(pos, p_begin)
            seg_end = min(cell_end, p_end)
            if seg_end > seg_begin:
                spans.append(
                    CellSpan(
                        vob_file=p,
                        start=seg_begin - p_begin,
                        end=seg_end - p_begin,
                        cell_nr=cell.cell_nr,
                    )
                )
                pos = seg_end
    return spans


def read_ifo(path: str) -> bytes:
    with open(path, "rb") as fh:
        return fh.read()


def vts_ifo_path(video_ts_dir: str, vts_nr: int) -> Optional[str]:
    """Return the path to ``VTS_nn_0.IFO`` for ``vts_nr``, or None if absent.

    Handles both upper- and lower-case naming as seen on real dumps.
    """
    for nm in (f"VTS_{vts_nr:02d}_0.IFO", f"vts_{vts_nr:02d}_0.ifo"):
        p = os.path.join(video_ts_dir, nm)
        if os.path.isfile(p):
            return p
    return None
