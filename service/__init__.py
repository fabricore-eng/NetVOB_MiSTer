"""NetVOB_MiSTer provider service (Raspberry Pi 5).

Source-agnostic MPEG-2 Program Stream provider. Owns the wire to the console
and emits one MPEG-2 Program Stream over TCP, filled from pluggable backends
behind a common ``Source`` interface.

See ``docs/service-design.md`` and ``docs/transport.md`` for the contracts.
"""
