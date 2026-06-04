"""PlexSource — the transcoded second library (STUB).

All Plex specifics (auth/token, the partly-private API, ffmpeg transcode
params, path mapping) are quarantined here. Library label "Plex", badge
``transcoded``.
"""

from service.sources.plex.plex import PlexSource

__all__ = ["PlexSource"]
