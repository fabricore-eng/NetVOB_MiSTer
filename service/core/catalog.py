"""Catalog aggregator — keeps libraries SEPARATE and labeled. No merge/dedupe.

Per ``service-design.md`` §2 and ``catalog-browse.md`` §1: the aggregator calls
each enabled source's ``browse()`` and keeps results in **separate, labeled
lists** (``{"DVD Dumps": [...], "Plex": [...]}``). A title that exists in both
libraries appears in both — by design. There is **no merge and no dedupe, ever**.

It also stamps each entry with its source's default ``badge`` if the entry did
not already carry one, so the UI can render fidelity chips uniformly.
"""

from __future__ import annotations

from typing import Optional

from service.sources.base.source import CatalogEntry, Source


class Catalog:
    """Aggregates multiple ``Source`` backends into separate labeled buckets."""

    def __init__(self, sources: Optional[list[Source]] = None) -> None:
        # Preserve registration order; that is also the UI display order.
        self._sources: list[Source] = []
        if sources:
            for src in sources:
                self.register(src)

    def register(self, source: Source) -> None:
        """Add a source. Duplicate library *names* are rejected so two
        libraries never silently collapse into one bucket."""
        for existing in self._sources:
            if existing.name == source.name:
                raise ValueError(
                    f"a source named {source.name!r} is already registered; "
                    "libraries are kept separate and must have distinct names"
                )
        self._sources.append(source)

    @property
    def sources(self) -> list[Source]:
        return list(self._sources)

    def browse(self, path: Optional[str] = None) -> dict[str, list[CatalogEntry]]:
        """Return ``{library_name: [entries...]}`` — one bucket per source.

        Buckets are independent: no merge, no dedupe across libraries. Each
        entry is badged with its source's default badge if it lacks one.
        """
        result: dict[str, list[CatalogEntry]] = {}
        for source in self._sources:
            entries = list(source.browse(path))
            for entry in entries:
                if not entry.badge:
                    entry.badge = source.badge
            # One bucket per source name, in registration order.
            result[source.name] = entries
        return result

    def find_source(self, name: str) -> Optional[Source]:
        """Look up a registered source by its library name."""
        for source in self._sources:
            if source.name == name:
                return source
        return None

    def health(self) -> dict[str, dict]:
        """Return ``{library_name: health_dict}`` for every source."""
        return {source.name: source.health() for source in self._sources}
