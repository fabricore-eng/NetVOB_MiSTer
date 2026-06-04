"""The Source plugin boundary: the ABC + the metadata dataclasses."""

from service.sources.base.source import (
    AvInfo,
    CatalogEntry,
    NavInfo,
    Source,
    StreamHandle,
)

__all__ = [
    "AvInfo",
    "CatalogEntry",
    "NavInfo",
    "Source",
    "StreamHandle",
]
