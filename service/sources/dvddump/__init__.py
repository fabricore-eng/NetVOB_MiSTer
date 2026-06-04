"""DVDDumpSource — the lossless, field-exact spine.

All DVD quirks (IFO/PGC/VOBU nav, nav-pack stripping, cell ordering) stay
quarantined here and never leak past ``open()``. Library label "DVD Dumps",
badge ``field-exact``.
"""

from service.sources.dvddump.dvddump import DVDDumpSource

__all__ = ["DVDDumpSource"]
