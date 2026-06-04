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

PGC (at ``pgcit_byte + pgc_start_byte``):
  +0x02 u8   nr_of_programs
  +0x03 u8   nr_of_cells
  +0x04 u32  playback_time (dvd_time_t: BCD hour, min, sec, frame; the frame
             byte's top 2 bits select the frame rate: 0b01=25, 0b11=29.97)
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
class VTSI:
    """Parsed Video Title Set information (``VTS_nn_0.IFO``)."""

    identifier: bytes
    nr_of_pgcs: int
    # First PGC summary (the title feature PGC for a one-PGC title set):
    duration_s: Optional[float] = None
    fps: Optional[float] = None
    nr_of_programs: Optional[int] = None
    nr_of_cells: Optional[int] = None

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


def parse_vtsi(buf: bytes) -> VTSI:
    """Parse a ``VTS_nn_0.IFO`` byte buffer into a :class:`VTSI`.

    Reads the first PGC's playback time / program / cell counts. Multi-PGC
    title sets are not fully walked (TODO); the first PGC is the feature PGC
    for the common one-title-per-VTS layout this targets.
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
    pgc = pgcit + pgc_start_byte
    if pgc + 8 > len(buf):
        raise IFOParseError(f"PGC at byte {pgc} past end ({len(buf)} bytes)")
    out.nr_of_programs = _u8(buf, pgc + 2)
    out.nr_of_cells = _u8(buf, pgc + 3)
    duration_s, fps = decode_dvd_time(buf[pgc + 4 : pgc + 8])
    out.duration_s = duration_s
    out.fps = fps
    return out


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
