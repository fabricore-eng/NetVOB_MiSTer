"""PlexSource — STUB. Conforms to the ``Source`` ABC; no creds available.

Per ``service-design.md`` §3.2: Plex is used as a **catalog + file source**;
we own the MPEG-2 encode (``ffmpeg ... -f vob`` → Program Stream) because Plex
does not natively transcode to MPEG-2. None of that is implemented here — no
``PLEX_URL`` / ``PLEX_TOKEN`` are available in this environment.

This stub:
* conforms to the ``Source`` ABC,
* ``health()`` reports **unconfigured**,
* ``browse()`` returns ``[]`` with a clear "set PLEX_URL/PLEX_TOKEN" note,
* ``open()`` raises ``NotImplementedError``.

All Plex specifics (auth/token refresh, the partly-private API, HLS/transcode
decision endpoints, path mapping, ffmpeg invocation) stay quarantined in this
module. Library label "Plex", badge ``transcoded``.
"""

from __future__ import annotations

import os
from typing import Optional

from service.sources.base.source import CatalogEntry, Source, StreamHandle

_UNCONFIGURED_NOTE = (
    "stub — set PLEX_URL/PLEX_TOKEN to enable the Plex library "
    "(catalog + ffmpeg transcode to 480i MPEG-2 PS)"
)


class PlexSource(Source):
    """Transcoded Plex backend — not implemented (no creds in this env)."""

    name = "Plex"
    badge = "transcoded"
    is_local_to_console = False

    def __init__(
        self,
        url: Optional[str] = None,
        token: Optional[str] = None,
    ) -> None:
        # Fall back to env so a real deployment can configure without code.
        self.url = url or os.environ.get("PLEX_URL")
        self.token = token or os.environ.get("PLEX_TOKEN")

    @property
    def configured(self) -> bool:
        return bool(self.url) and bool(self.token)

    def browse(self, path: Optional[str] = None) -> list[CatalogEntry]:
        """Stub: return an empty catalog until creds are provided.

        TODO(plex): query the Plex API for libraries/sections/items and map to
        ``CatalogEntry`` with posters/titles/season/episode (Plex's rich
        metadata is the feature here). Quarantined to this module.
        """
        # Empty list regardless; the note travels via health()/status.
        return []

    def open(self, id: str) -> StreamHandle:
        """Stub: not implemented.

        TODO(plex): pull the original media (download/parts endpoint or a Pi-
        local path) and transcode to 480i MPEG-2 PS with ffmpeg
        (``mpeg2video`` + ``ac3``/``mp2``, ``-f vob``/``-f dvd``), exposing a
        ``StreamHandle`` over the ffmpeg stdout. Not available without creds.
        """
        raise NotImplementedError(_UNCONFIGURED_NOTE)

    def health(self) -> dict:
        return {
            "name": self.name,
            "badge": self.badge,
            "reachable": False,
            "configured": self.configured,
            "note": (
                "configured but not implemented (stub)"
                if self.configured
                else _UNCONFIGURED_NOTE
            ),
        }
