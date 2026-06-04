"""Service core: catalog aggregator, control protocol, streamer.

The core owns the wire and the control protocol. It never imports a Plex or a
DVD type; the only contract it knows is "MPEG-2 Program Stream + a little
metadata" (the ``Source`` ABC and its dataclasses).
"""
