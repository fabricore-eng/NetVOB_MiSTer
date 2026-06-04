"""The ``Source`` interface — the plugin boundary.

Implements the contract from ``docs/service-design.md`` §1. Two methods are the
real contract (``browse``, ``open``); the rest is metadata/lifecycle. The whole
point of this seam is that a new backend is "add a class," never a refactor.

**Key property:** ``open(id)`` returns MPEG-2 Program Stream bytes regardless of
backend. Whatever a backend does to get there (DVD demux/nav-strip, Plex
transcode) is its own business. The core never imports a Plex or a DVD type.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CatalogEntry:
    """One browsable item, as surfaced to the UI / control channel.

    Fields mirror ``service-design.md`` §1 exactly.
    """

    id: str
    """Opaque, source-scoped id, e.g. ``"dvddump:matrix-1999"``."""

    title: str
    kind: str
    """``"movie" | "episode" | "title" | ...``"""

    duration_s: Optional[float] = None
    poster_url: Optional[str] = None
    """Plex has these; dumps usually don't."""

    badge: str = ""
    """``"field-exact"`` (dump) | ``"transcoded"`` (Plex) — see catalog-browse.md."""

    extra: dict = field(default_factory=dict)
    """Source-specific (season/ep, disc title #, chapters, ...)."""


@dataclass
class AvInfo:
    """Audio/video shape of a stream — what the streamer/ARM need for sync.

    Per ``service-design.md`` §1: ``video: mpeg2; audio: ac3|mp2|none; pts
    base; field cadence hint``.
    """

    video: str = "mpeg2"
    """Video codec on the wire. Always ``"mpeg2"`` for this project."""

    audio: str = "none"
    """``"ac3" | "mp2" | "none"`` — the audio codec multiplexed in the PS."""

    pts_base: Optional[int] = None
    """First/base PTS (90 kHz ticks) if known, for A/V sync setup; else None."""

    field_cadence: str = "interlaced"
    """Field cadence hint: ``"interlaced"`` (480i), ``"progressive"``,
    ``"telecine"`` (3:2 pulldown / film), or ``"unknown"``."""


@dataclass
class NavInfo:
    """Seek index (GOP/VOBU) exposed by sources that have one (DVD).

    ``entries`` is a list of ``(time_seconds, byte_offset)`` seek points sorted
    by time, each landing on an I-frame / GOP / VOBU boundary so seeking stays
    field-exact. None / empty for sources without an index (e.g. live Plex
    transcode, which snaps to keyframes itself).
    """

    entries: list[tuple[float, int]] = field(default_factory=list)

    def nearest_preceding(self, t_seconds: float) -> Optional[tuple[float, int]]:
        """Return the ``(time, offset)`` seek point at-or-before ``t_seconds``.

        Returns ``None`` if there is no entry at-or-before ``t`` (e.g. empty
        index, or ``t`` precedes the first entry).
        """
        best: Optional[tuple[float, int]] = None
        for entry in self.entries:
            if entry[0] <= t_seconds:
                if best is None or entry[0] > best[0]:
                    best = entry
            else:
                # entries are sorted by time; nothing later can be <= t
                break
        return best


class StreamHandle(ABC):
    """A pull iterator of MPEG-2 Program Stream bytes + sync/seek metadata.

    Per ``service-design.md`` §1. ``read(n)`` yields the next PS bytes
    (back-pressured); ``seek(t)`` jumps to the nearest preceding I-frame/GOP;
    ``close()`` releases resources.

    Concrete attributes ``duration_s``, ``nav`` and ``av`` are set by the
    implementation in ``__init__``.
    """

    duration_s: Optional[float]
    nav: Optional[NavInfo]
    av: AvInfo

    @abstractmethod
    def read(self, n: int) -> bytes:
        """Return up to ``n`` next PS bytes. Empty ``bytes`` signals EOF."""
        raise NotImplementedError

    @abstractmethod
    def seek(self, t_seconds: float) -> None:
        """Seek to the nearest preceding I-frame/GOP boundary."""
        raise NotImplementedError

    @abstractmethod
    def close(self) -> None:
        """Release any underlying resources (files, sockets, subprocess)."""
        raise NotImplementedError

    # Context-manager sugar so ``with source.open(id) as h:`` works and always
    # closes. Not part of the documented contract but free and harmless.
    def __enter__(self) -> "StreamHandle":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class Source(ABC):
    """A backend plugin. Two methods are the contract: ``browse`` and ``open``.

    Class attributes ``name``, ``badge`` and ``is_local_to_console`` describe
    the library; concrete sources set them as class attributes.
    """

    name: str = ""
    """Stable library name, e.g. ``"DVD Dumps"``, ``"Plex"``."""

    badge: str = ""
    """Default badge applied to this source's entries
    (``"field-exact"`` / ``"transcoded"``)."""

    is_local_to_console: bool = False
    """False for network plugins (Pi-side); True for future Disc/SSD (ARM-side)."""

    @abstractmethod
    def browse(self, path: Optional[str] = None) -> list[CatalogEntry]:
        """List this source's catalog (optionally under ``path``)."""
        raise NotImplementedError

    @abstractmethod
    def open(self, id: str) -> StreamHandle:
        """Open ``id`` and return a ``StreamHandle`` of MPEG-2 PS bytes."""
        raise NotImplementedError

    @abstractmethod
    def health(self) -> dict:
        """Report reachability/auth for the UI, e.g.
        ``{"reachable": bool, "configured": bool, "note": str}``."""
        raise NotImplementedError
