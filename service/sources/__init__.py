"""Pluggable backends (the plugin boundary).

Each backend implements the ``Source`` ABC from ``service.sources.base``.
A backend's quirks (DVD nav, Plex auth) stay quarantined inside its package
and never leak past ``open()`` / ``browse()``.
"""
