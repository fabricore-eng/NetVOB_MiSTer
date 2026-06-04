"""DVDDumpSource + a pure-Python MPEG-2 Program Stream walker.

The **verifiable-now** core (per the task brief and ``service-design.md`` §3.1):

* **PS passthrough**: a VOB *is* an MPEG-2 Program Stream, so streaming is a
  byte copy.
* **DVD nav-pack stripping**: strip the ``private_stream_2`` PCI/DSI navigation
  PES packets (``stream_id == 0xBF``) so the wire carries a clean PS, while
  passing **every other pack/PES through losslessly, byte-for-byte**.

Real IFO/PGC/VOBU parsing (``libdvdread``) is **not installed**, so ``browse()``
enumerates ``.VOB`` files from a folder with a clear TODO. The PS-passthrough +
nav-strip path is **real and unit-tested**.

Library label: ``"DVD Dumps"``, badge ``"field-exact"``.

------------------------------------------------------------------------------
MPEG-2 Program Stream structure (ISO/IEC 13818-1), the bits we walk
------------------------------------------------------------------------------
A PS is a sequence of **packs**. Each starts with a 4-byte start code
``00 00 01 xx``:

  * ``BA`` pack_header        — SCR + mux rate; MPEG-2 form is 14 bytes plus
                                ``pack_stuffing_length`` (low 3 bits of byte 13)
                                stuffing bytes.
  * ``BB`` system_header      — ``00 00 01 BB`` + 2-byte length + that many bytes.
  * ``B9`` MPEG_program_end_code — 4 bytes, ends the stream.
  * any other ``stream_id``   — a **PES packet**: ``00 00 01 sid`` + 2-byte
                                ``PES_packet_length`` + that many payload bytes.

DVD nav-packs are ordinary packs whose payload includes ``private_stream_2``
(``stream_id == 0xBF``) PES packets carrying PCI (presentation control) and DSI
(data search) info. We drop exactly those PES packets and keep the rest.
"""

from __future__ import annotations

import os
from typing import Iterator, Optional

from service.sources.base.source import (
    AvInfo,
    CatalogEntry,
    NavInfo,
    Source,
    StreamHandle,
)

# Start-code constants (the byte after 00 00 01).
PACK_START_CODE = 0xBA
SYSTEM_HEADER_START_CODE = 0xBB
PROGRAM_END_CODE = 0xB9
PRIVATE_STREAM_2 = 0xBF  # DVD PCI/DSI nav packets — the thing we strip.
PADDING_STREAM = 0xBE


class PSParseError(ValueError):
    """Malformed Program Stream encountered while walking."""


def _read_u16(buf: bytes, i: int) -> int:
    return (buf[i] << 8) | buf[i + 1]


def iter_ps_units(buf: bytes) -> Iterator[tuple[int, int, int]]:
    """Walk ``buf`` as an MPEG-2 PS, yielding ``(start_code, begin, end)``.

    Each tuple is one top-level unit: ``start_code`` is the byte after the
    ``00 00 01`` prefix, ``begin``/``end`` bound the full unit (including its
    start code and any payload/stuffing) so ``buf[begin:end]`` reproduces it
    exactly. Packs are split into their header + the PES/system units inside.

    Raises ``PSParseError`` on a malformed/truncated stream.
    """
    n = len(buf)
    i = 0
    while i < n:
        # Expect a start-code prefix 00 00 01 here.
        if i + 4 > n:
            raise PSParseError(f"truncated start code at offset {i}")
        if buf[i] != 0x00 or buf[i + 1] != 0x00 or buf[i + 2] != 0x01:
            raise PSParseError(
                f"expected start-code prefix at offset {i}, "
                f"got {buf[i:i+4].hex()}"
            )
        sid = buf[i + 3]

        if sid == PACK_START_CODE:
            # MPEG-2 pack header: 14 fixed bytes + stuffing.
            if i + 14 > n:
                raise PSParseError(f"truncated pack header at offset {i}")
            # Distinguish MPEG-2 (top 2 bits of byte 4 == 0b01) from MPEG-1
            # (top 4 bits == 0b0010). We support the MPEG-2/DVD form.
            marker = buf[i + 4] >> 6
            if marker == 0b01:
                stuffing = buf[i + 13] & 0x07
                end = i + 14 + stuffing
            elif (buf[i + 4] >> 4) == 0b0010:
                # MPEG-1 pack header is 12 bytes, no stuffing field.
                end = i + 12
            else:
                raise PSParseError(
                    f"unrecognized pack header at offset {i}: "
                    f"byte4={buf[i+4]:#04x}"
                )
            if end > n:
                raise PSParseError(f"truncated pack stuffing at offset {i}")
            yield (PACK_START_CODE, i, end)
            i = end
            continue

        if sid == PROGRAM_END_CODE:
            # 4 bytes, terminates the stream.
            yield (PROGRAM_END_CODE, i, i + 4)
            i += 4
            continue

        # Everything else (system header 0xBB and all PES 0xC0..0xFF, 0xBD,
        # 0xBE, 0xBF) is length-prefixed: 4-byte start + 2-byte length + body.
        if i + 6 > n:
            raise PSParseError(f"truncated PES/system length at offset {i}")
        length = _read_u16(buf, i + 4)
        end = i + 6 + length
        if end > n:
            raise PSParseError(
                f"PES/system packet at offset {i} runs past end "
                f"(needs {end}, have {n})"
            )
        yield (sid, i, end)
        i = end


def strip_nav_packets(buf: bytes) -> bytes:
    """Return ``buf`` with every ``private_stream_2`` (0xBF) PES packet removed.

    All other packs / PES packets / system headers / the program-end code are
    copied through **byte-for-byte**. This is the lossless DVD nav-pack strip:
    PCI/DSI navigation is dropped; the audio/video/SCR spine is untouched.
    """
    out = bytearray()
    for sid, begin, end in iter_ps_units(buf):
        if sid == PRIVATE_STREAM_2:
            continue  # drop the nav packet
        out += buf[begin:end]
    return bytes(out)


class DVDStreamHandle(StreamHandle):
    """A pull iterator of nav-stripped MPEG-2 PS bytes for one title.

    The whole title's bytes are nav-stripped up front (cheap, byte-copy) and
    served via ``read(n)``. ``seek`` uses the ``NavInfo`` index when present.
    """

    def __init__(
        self,
        ps_bytes: bytes,
        *,
        duration_s: Optional[float] = None,
        nav: Optional[NavInfo] = None,
        av: Optional[AvInfo] = None,
    ) -> None:
        self._data = ps_bytes
        self._pos = 0
        self.duration_s = duration_s
        self.nav = nav
        self.av = av or AvInfo(
            video="mpeg2", audio="ac3", field_cadence="interlaced"
        )
        self._closed = False

    def read(self, n: int) -> bytes:
        if self._closed:
            raise ValueError("read on a closed handle")
        if n <= 0:
            return b""
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def seek(self, t_seconds: float) -> None:
        if self._closed:
            raise ValueError("seek on a closed handle")
        if self.nav is not None:
            target = self.nav.nearest_preceding(t_seconds)
            if target is not None:
                self._pos = target[1]
                return
        # No usable index: clamp to start (a real impl would use the time map).
        self._pos = 0

    def close(self) -> None:
        self._closed = True
        self._data = b""

    # Test/diagnostic helper: bytes remaining from the current position.
    def remaining(self) -> int:
        return max(0, len(self._data) - self._pos)


class DVDDumpSource(Source):
    """Field-exact DVD-dump backend (PS passthrough + nav-pack stripping).

    Parameters
    ----------
    root:
        Folder of dumps. ``browse()`` enumerates ``*.VOB`` here (stub). The
        ``open()`` / nav-strip path is real regardless of ``root``.
    """

    name = "DVD Dumps"
    badge = "field-exact"
    is_local_to_console = False

    def __init__(self, root: Optional[str] = None) -> None:
        self.root = root

    def browse(self, path: Optional[str] = None) -> list[CatalogEntry]:
        """Enumerate ``.VOB`` files from the dump folder.

        TODO(libdvdread): real title enumeration requires parsing the IFO/PGC/
        VOBU structure (``libdvdread``) to list *titles* (title sets, angles,
        durations, chapters) rather than raw files, plus disc-ID metadata
        lookup (see ``docs/catalog-browse.md`` §5). ``libdvdread`` is not
        installed in this environment, so for now we list ``.VOB`` files so the
        library is browsable. This stub never affects the stream path.
        """
        folder = path or self.root
        if not folder or not os.path.isdir(folder):
            return []
        entries: list[CatalogEntry] = []
        for fname in sorted(os.listdir(folder)):
            if not fname.upper().endswith(".VOB"):
                continue
            full = os.path.join(folder, fname)
            stem = os.path.splitext(fname)[0]
            entries.append(
                CatalogEntry(
                    id=f"dvddump:{stem}",
                    title=stem,  # TODO(metadata): disc-ID lookup -> real title
                    kind="title",
                    duration_s=None,  # TODO(libdvdread): from IFO/PGC
                    poster_url=None,
                    badge=self.badge,
                    extra={"path": full, "stub": True},
                )
            )
        return entries

    def open(self, id: str) -> StreamHandle:
        """Open ``id`` and return a nav-stripped MPEG-2 PS handle.

        Resolves ``dvddump:<stem>`` to ``<stem>.VOB`` under ``root``, reads it,
        strips ``private_stream_2`` nav packets, and serves the clean PS.

        Real impl TODO: follow IFO/PGC/VOBU navigation to read the selected
        title's cells in order and build the VOBU/GOP ``NavInfo`` index. Here
        we treat one ``.VOB`` as the title and strip nav packets — the
        verifiable-now path.
        """
        if not id.startswith("dvddump:"):
            raise ValueError(f"not a dvddump id: {id!r}")
        stem = id[len("dvddump:") :]
        if not self.root:
            raise FileNotFoundError(
                f"no dump root configured; cannot open {id!r}"
            )
        full = os.path.join(self.root, f"{stem}.VOB")
        if not os.path.isfile(full):
            # Allow exact-case .vob too.
            alt = os.path.join(self.root, f"{stem}.vob")
            if os.path.isfile(alt):
                full = alt
            else:
                raise FileNotFoundError(f"no VOB for {id!r} at {full}")
        with open(full, "rb") as fh:
            raw = fh.read()
        clean = strip_nav_packets(raw)
        return self.open_bytes(clean)

    def open_bytes(
        self,
        ps_bytes: bytes,
        *,
        already_stripped: bool = True,
        duration_s: Optional[float] = None,
        nav: Optional[NavInfo] = None,
        av: Optional[AvInfo] = None,
    ) -> DVDStreamHandle:
        """Build a handle directly from PS bytes (test/in-memory entry point).

        If ``already_stripped`` is False, nav packets are stripped here first.
        """
        data = ps_bytes if already_stripped else strip_nav_packets(ps_bytes)
        return DVDStreamHandle(
            data, duration_s=duration_s, nav=nav, av=av
        )

    def health(self) -> dict:
        configured = bool(self.root) and os.path.isdir(self.root or "")
        return {
            "name": self.name,
            "badge": self.badge,
            "reachable": configured,
            "configured": configured,
            "root": self.root,
            "note": (
                "ready (PS passthrough + nav-strip)"
                if configured
                else "no dump root configured; set a VIDEO_TS/VOB folder"
            ),
        }
