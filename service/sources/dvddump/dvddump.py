"""DVDDumpSource + a pure-Python MPEG-2 Program Stream walker.

The **verifiable-now** core (per the task brief and ``service-design.md`` §3.1):

* **PS passthrough**: a VOB *is* an MPEG-2 Program Stream, so streaming is a
  byte copy.
* **DVD nav-pack stripping**: strip the ``private_stream_2`` PCI/DSI navigation
  PES packets (``stream_id == 0xBF``) so the wire carries a clean PS, while
  passing **every other pack/PES through losslessly, byte-for-byte**.

``browse()`` parses the DVD's own IFO navigation metadata (a pure-Python VMGI +
VTSI parser, **no** ``libdvdread``) to enumerate real *titles* — title sets,
which VTS each maps to, chapter/angle counts and PGC playback durations — and
flags the longest as the main feature. If no ``VIDEO_TS.IFO`` is present it
falls back to listing ``.VOB`` files. The PS-passthrough + nav-strip path is
**real and unit-tested**.

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
from service.sources.dvddump.ifo import (
    IFOParseError,
    parse_vmgi,
    parse_vtsi,
    read_ifo,
    vts_ifo_path,
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
        """Enumerate real DVD *titles* from the IFO navigation metadata.

        Reads ``VIDEO_TS.IFO`` (VMGI) to list every title and the VTS it maps
        to, then each referenced ``VTS_nn_0.IFO`` (VTSI) for the PGC playback
        duration and chapter count. The longest title is flagged as the main
        feature (``extra["main_feature"]``). If no ``VIDEO_TS.IFO`` is present
        (or it cannot be parsed), falls back to listing ``.VOB`` files so the
        library is still browsable. Neither path ever affects the stream path.

        TODO(metadata): human title naming still needs a disc-ID lookup (see
        ``docs/catalog-browse.md`` §5) — IFOs carry no human-readable titles, so
        ``title`` is a structural label (``"Title 1 (main feature)"``).
        TODO(pgc): multi-PGC title sets report only their first PGC's duration.
        """
        folder = path or self.root
        if not folder or not os.path.isdir(folder):
            return []
        entries = self._browse_ifo(folder)
        if entries is not None:
            return entries
        return self._browse_vob_fallback(folder)

    def _browse_ifo(self, folder: str) -> Optional[list[CatalogEntry]]:
        """Real IFO-driven title enumeration. None => no/unparseable VMG IFO."""
        vmg_path = None
        for nm in ("VIDEO_TS.IFO", "video_ts.ifo"):
            cand = os.path.join(folder, nm)
            if os.path.isfile(cand):
                vmg_path = cand
                break
        if vmg_path is None:
            return None
        try:
            vmgi = parse_vmgi(read_ifo(vmg_path))
        except (IFOParseError, OSError):
            return None
        if not vmgi.is_valid or not vmgi.titles:
            return None

        # Per-VTS PGC summary, parsed once and cached.
        vts_cache: dict[int, object] = {}

        def vts_info(vts_nr: int):
            if vts_nr not in vts_cache:
                p = vts_ifo_path(folder, vts_nr)
                info = None
                if p:
                    try:
                        info = parse_vtsi(read_ifo(p))
                    except (IFOParseError, OSError):
                        info = None
                vts_cache[vts_nr] = info
            return vts_cache[vts_nr]

        # First pass: durations, to pick the longest title as the main feature.
        durations: list[Optional[float]] = []
        for t in vmgi.titles:
            info = vts_info(t.vts_nr)
            durations.append(getattr(info, "duration_s", None) if info else None)
        main_idx = -1
        best = -1.0
        for i, d in enumerate(durations):
            if d is not None and d > best:
                best, main_idx = d, i

        entries: list[CatalogEntry] = []
        for i, t in enumerate(vmgi.titles):
            info = vts_info(t.vts_nr)
            is_main = i == main_idx
            label = f"Title {t.title_nr}"
            if is_main:
                label += " (main feature)"
            entries.append(
                CatalogEntry(
                    id=f"dvddump:VTS_{t.vts_nr:02d}_1",
                    title=label,
                    kind="title",
                    duration_s=durations[i],
                    poster_url=None,
                    badge=self.badge,
                    extra={
                        "title_nr": t.title_nr,
                        "vts_nr": t.vts_nr,
                        "vts_ttn": t.vts_ttn,
                        "chapters": t.nr_of_chapters,
                        "angles": t.nr_of_angles,
                        "main_feature": is_main,
                        "nr_of_pgcs": getattr(info, "nr_of_pgcs", None)
                        if info
                        else None,
                        "fps": getattr(info, "fps", None) if info else None,
                        "source": "ifo",
                    },
                )
            )
        return entries

    def _browse_vob_fallback(self, folder: str) -> list[CatalogEntry]:
        """List ``.VOB`` files when there is no parseable ``VIDEO_TS.IFO``."""
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
                    duration_s=None,
                    poster_url=None,
                    badge=self.badge,
                    extra={"path": full, "source": "vob-fallback", "stub": True},
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
