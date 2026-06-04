"""NetVOB DVD-dump disc-ID + title-metadata resolver (M4, docs/catalog-browse.md Section 5).

Self-contained: reads raw IFO bytes directly, depends only on the stdlib, and does
not couple to ``service/``. The DVDDumpSource plugin can import these helpers.
"""

from .discid import disc_fingerprint, FINGERPRINT_VERSION
from .metadata import resolve_title, cache_put, parse_folder_title

__all__ = [
    "disc_fingerprint",
    "FINGERPRINT_VERSION",
    "resolve_title",
    "cache_put",
    "parse_folder_title",
]
