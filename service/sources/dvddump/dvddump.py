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
import re
import threading
from typing import Iterator, Optional

from service.sources.base.source import (
    AvInfo,
    CatalogEntry,
    NavInfo,
    Source,
    StreamHandle,
)
from service.sources.dvddump.ifo import (
    SECTOR,
    CellPlayback,
    CellSpan,
    IFOParseError,
    parse_vmgi,
    parse_vtsi,
    read_ifo,
    resolve_cell_spans,
    vts_ifo_path,
    vts_vob_parts,
)
from service.sources.dvddump.discid import try_disc_id

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
        # Serializes read()/seek()/close() so a control-thread seek() can't
        # race the pump's read() and mix pre-/post-seek bytes (garbled PS).
        # read() is in-memory (non-blocking), so holding the lock can't
        # deadlock a concurrent seek.
        self._lock = threading.Lock()

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        with self._lock:
            if self._closed:
                raise ValueError("read on a closed handle")
            chunk = self._data[self._pos : self._pos + n]
            self._pos += len(chunk)
            return chunk

    def seek(self, t_seconds: float) -> None:
        with self._lock:
            if self._closed:
                raise ValueError("seek on a closed handle")
            if self.nav is not None:
                target = self.nav.nearest_preceding(t_seconds)
                if target is not None:
                    self._pos = target[1]
                    return
            # No usable index: clamp to start (a real impl would time-map).
            self._pos = 0

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._data = b""

    # Test/diagnostic helper: bytes remaining from the current position.
    def remaining(self) -> int:
        with self._lock:
            return max(0, len(self._data) - self._pos)


def navinfo_from_cells(cells: list[CellPlayback]) -> NavInfo:
    """Build a coarse seek index from PGC cell boundaries.

    This is the **cell-granular** index, derived purely from the IFO's cell
    playback table: each cell start is a guaranteed I-frame/VOBU boundary, so it
    is a safe field-exact seek landing (transport.md §6). The ``byte_offset`` is
    the offset into the **nav-stripped** title stream — but we cannot know the
    post-strip offset without walking, so we record the cell's offset in the
    *pre-strip* cell address space and let the handle translate. Time is the
    cumulative cell playback time.

    NOTE: this is coarser than the DSI/time-map VOBU index (one entry per cell,
    not per ~0.5 s VOBU). The finer time-map parse is a TODO (see
    ``StreamHandle.nav`` docstring in :class:`DVDCellStreamHandle`).
    """
    entries: list[tuple[float, int]] = []
    t = 0.0
    raw_off = 0
    for cell in cells:
        entries.append((t, raw_off))
        if cell.playback_time_s is not None:
            t += cell.playback_time_s
        else:
            # No PGC playback time for this cell (unspecified/zero fps in the
            # IFO). Advancing t by 0 would make this and the next cell share an
            # identical timestamp -> a non-monotonic seek map where a seek into
            # the gap mis-lands (nearest_preceding can't disambiguate equal
            # times). Estimate a non-zero duration from the cell's size at a
            # nominal DVD-Video bitrate so t stays STRICTLY increasing and the
            # landing is at least proportionate. Coarse by design (see NOTE).
            t += _estimate_cell_seconds(cell.nr_sectors)
        raw_off += cell.nr_sectors * SECTOR
    return NavInfo(entries=entries)


# Nominal DVD-Video program bitrate (bytes/s) for estimating a cell's duration
# when the IFO gives no playback time. ~5 Mbit/s is a mid-range average (peak is
# ~10.08 Mbit/s); only used to keep the seek map monotonic, never for A/V sync.
_NOMINAL_DVD_BYTES_PER_S = 5_000_000 / 8


def _estimate_cell_seconds(nr_sectors: int) -> float:
    """Estimate a cell's playback seconds from its sector count.

    Returns a strictly positive value for any non-empty cell so the cumulative
    seek-map time advances (preserving monotonicity); 0.0 only for an empty
    cell (which also adds no byte offset, so the duplicate entry is harmless).
    """
    if nr_sectors <= 0:
        return 0.0
    return (nr_sectors * SECTOR) / _NOMINAL_DVD_BYTES_PER_S


class DVDCellStreamHandle(StreamHandle):
    """Stream a title's PGC cells **in order**, nav-stripped, from VOB files.

    Reads each :class:`CellSpan` lazily (one whole span at a time — spans are
    sector-aligned and every DVD pack lies wholly within a sector, so stripping
    a span in isolation is correct), applies :func:`strip_nav_packets`, and
    serves the clean PS via ``read(n)``. This is the M3 ordered-cell path: the
    bytes that go on the wire are exactly the title's cells in PGC order with
    the ``private_stream_2`` PCI/DSI nav packets removed.

    ``nav`` carries the **cell-granular** seek index. Its byte offsets are in the
    *pre-strip* address space; ``seek`` maps a time to the cell at-or-before it
    and resets streaming to that cell.

    TODO(time-map): a finer VOBU/GOP index from the DSI packets' time map (per
    transport.md §6) would give ~0.5 s seek granularity. Parsing DSI is deferred
    this cycle — the cell index is correct and field-exact, just coarse.
    """

    def __init__(
        self,
        spans: list[CellSpan],
        *,
        cells: Optional[list[CellPlayback]] = None,
        duration_s: Optional[float] = None,
        nav: Optional[NavInfo] = None,
        av: Optional[AvInfo] = None,
    ) -> None:
        self._spans = spans
        self._cells = cells or []
        # Map cell_nr -> index of its first span, for seek().
        self._cell_first_span: dict[int, int] = {}
        for i, sp in enumerate(spans):
            self._cell_first_span.setdefault(sp.cell_nr, i)
        self._span_idx = 0
        self._buf = b""  # stripped bytes pending delivery
        self._buf_pos = 0
        self.duration_s = duration_s
        self.nav = nav
        self.av = av or AvInfo(
            video="mpeg2", audio="ac3", field_cadence="interlaced"
        )
        self._closed = False
        # read()/seek()/close() all mutate (_buf, _buf_pos, _span_idx). The
        # server's control thread can seek() while the streamer pump is inside
        # read() -> a data race that mixes pre-/post-seek bytes (garbled PS).
        # This lock makes them mutually exclusive. read() here never blocks on
        # external I/O (file reads are local + bounded), so holding it across a
        # read can't deadlock a concurrent seek (unlike a network/gated handle,
        # whose own internal sync is its responsibility).
        self._lock = threading.Lock()

    def _fill(self) -> bool:
        """Load + strip the next span into the pending buffer. False at EOF."""
        while self._span_idx < len(self._spans):
            sp = self._spans[self._span_idx]
            self._span_idx += 1
            with open(sp.vob_file, "rb") as fh:
                fh.seek(sp.start)
                raw = fh.read(sp.length)
            clean = strip_nav_packets(raw)
            if clean:
                self._buf = clean
                self._buf_pos = 0
                return True
        return False

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        with self._lock:
            if self._closed:
                raise ValueError("read on a closed handle")
            out = bytearray()
            while len(out) < n:
                if self._buf_pos >= len(self._buf):
                    if not self._fill():
                        break  # EOF
                take = min(n - len(out), len(self._buf) - self._buf_pos)
                out += self._buf[self._buf_pos : self._buf_pos + take]
                self._buf_pos += take
            return bytes(out)

    def seek(self, t_seconds: float) -> None:
        with self._lock:
            if self._closed:
                raise ValueError("seek on a closed handle")
            target_cell_nr: Optional[int] = None
            if self.nav is not None and self._cells:
                # nav entries are (time, raw_offset) per cell, in cell order.
                best_i = -1
                for i, (ct, _) in enumerate(self.nav.entries):
                    if ct <= t_seconds:
                        best_i = i
                    else:
                        break
                if best_i >= 0 and best_i < len(self._cells):
                    target_cell_nr = self._cells[best_i].cell_nr
            if target_cell_nr is None and self._spans:
                target_cell_nr = self._spans[0].cell_nr
            if target_cell_nr is not None:
                self._span_idx = self._cell_first_span.get(target_cell_nr, 0)
            else:
                self._span_idx = 0
            self._buf = b""
            self._buf_pos = 0

    def close(self) -> None:
        with self._lock:
            self._closed = True
            self._spans = []
            self._buf = b""


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

        Every entry is stamped with ``extra["disc_id"]`` — a stable, content-derived
        fingerprint of the disc's IFO set (see ``discid.py`` / ``catalog-browse.md``
        §5). It survives the folder being renamed and is identical across copies of the
        same dump, so the UI can group a disc's titles and detect re-dumps. ``None`` for
        a folder with no DVD structure (e.g. a bare-``.VOB`` fallback dir).

        TODO(metadata): mapping the ``disc_id`` to a *human* title still needs an
        external lookup (see ``docs/catalog-browse.md`` §5) — IFOs carry no
        human-readable titles, so ``title`` stays a structural label
        (``"Title 1 (main feature)"``).
        TODO(pgc): multi-PGC title sets report only their first PGC's duration.
        """
        folder = path or self.root
        if not folder or not os.path.isdir(folder):
            return []
        disc_id = try_disc_id(folder)  # stable disc identity, or None if no IFOs
        entries = self._browse_ifo(folder)
        if entries is None:
            entries = self._browse_vob_fallback(folder)
        for entry in entries:
            entry.extra["disc_id"] = disc_id
        return entries

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
                    title=stem,  # structural label; disc_id stamped by browse()
                    kind="title",
                    duration_s=None,
                    poster_url=None,
                    badge=self.badge,
                    extra={"path": full, "source": "vob-fallback", "stub": True},
                )
            )
        return entries

    # Matches the title ids browse() emits: dvddump:VTS_<nn>_<ttn>.
    _VTS_ID_RE = re.compile(r"^VTS_(\d+)_(\d+)$", re.IGNORECASE)

    def open(self, id: str) -> StreamHandle:
        """Open ``id`` and return a nav-stripped MPEG-2 PS handle.

        Two id forms:

        * ``dvddump:VTS_<nn>_<ttn>`` (what ``browse()`` emits) — the **M3
          ordered-cell path**: parse the VTS IFO's PGC, resolve its cells to
          per-file byte spans (:func:`cell_spans_for_title`), and stream them
          **in PGC playback order**, nav-stripped, with a cell-granular
          :class:`NavInfo` seek index.
        * ``dvddump:<stem>`` — legacy single-``.VOB`` path: read ``<stem>.VOB``,
          strip nav packets, serve the clean PS (kept for the VOB fallback).
        """
        if not id.startswith("dvddump:"):
            raise ValueError(f"not a dvddump id: {id!r}")
        stem = id[len("dvddump:") :]
        if not self.root:
            raise FileNotFoundError(
                f"no dump root configured; cannot open {id!r}"
            )

        m = self._VTS_ID_RE.match(stem)
        if m:
            vts_nr = int(m.group(1))
            # Ordered-cell path only when the VTS IFO is present (a real dump).
            # Without it we cannot know PGC/cell order, so fall through to the
            # legacy single-VOB path (also what the synthetic VOB tests rely on).
            if vts_ifo_path(self.root, vts_nr) is not None:
                return self._open_title(vts_nr)

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

    def cell_spans_for_title(
        self, vts_nr: int
    ) -> tuple[list[CellPlayback], list[CellSpan]]:
        """Resolve a VTS title to its ordered (cells, per-file byte spans).

        Parses ``VTS_<nn>_0.IFO``'s first PGC, then maps each cell's
        VTS-relative sector range onto the on-disk ``VTS_<nn>_1.VOB`` .. set.
        Returns the cells (PGC order) and the flat span list ``open()`` streams.
        Raises ``FileNotFoundError`` / ``IFOParseError`` on missing IFO/VOBs.
        """
        if not self.root:
            raise FileNotFoundError("no dump root configured")
        ifo_path = vts_ifo_path(self.root, vts_nr)
        if ifo_path is None:
            raise FileNotFoundError(
                f"no VTS_{vts_nr:02d}_0.IFO under {self.root!r}"
            )
        vtsi = parse_vtsi(read_ifo(ifo_path))
        if not vtsi.is_valid or vtsi.pgc is None or not vtsi.pgc.cells:
            raise IFOParseError(
                f"VTS {vts_nr} has no parseable PGC cells"
            )
        parts = vts_vob_parts(self.root, vts_nr)
        if not parts:
            raise FileNotFoundError(
                f"no VTS_{vts_nr:02d}_1.VOB title VOBs under {self.root!r}"
            )
        spans = resolve_cell_spans(vtsi.pgc.cells, parts)
        return vtsi.pgc.cells, spans

    def _open_title(self, vts_nr: int) -> DVDCellStreamHandle:
        cells, spans = self.cell_spans_for_title(vts_nr)
        ifo_path = vts_ifo_path(self.root, vts_nr)
        vtsi = parse_vtsi(read_ifo(ifo_path)) if ifo_path else None
        nav = navinfo_from_cells(cells)
        av = AvInfo(
            video="mpeg2",
            audio="ac3",
            field_cadence="interlaced",
        )
        return DVDCellStreamHandle(
            spans,
            cells=cells,
            duration_s=getattr(vtsi, "duration_s", None),
            nav=nav,
            av=av,
        )

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
