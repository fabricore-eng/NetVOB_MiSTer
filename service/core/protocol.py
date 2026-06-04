"""Control-channel JSON protocol + media-socket session preamble.

Per ``service-design.md`` §2 and ``transport.md`` §2:

* **Control channel** (separate TCP/WebSocket, JSON), carrying these messages:
  ``browse``, ``play{id}``, ``pause``, ``resume``, ``seek{t}``, ``stop``,
  ``status``. Decoupling control from media keeps seek/pause snappy and lets
  the UI query catalogs without touching the media socket.

* **Media socket** is a raw PS byte stream — *no* custom per-packet framing
  (PS is self-delimiting via pack/PES start codes). A tiny **session preamble**
  (stream id, ``AvInfo``, duration, nav-index availability) is sent **once** at
  ``open`` so the consumer can pipe the rest into off-the-shelf PS tooling.

Encoding is line-delimited JSON ("JSON Lines"): each message is a single
UTF-8 JSON object terminated by ``b"\\n"``. This is trivially framed over TCP
and human-debuggable. ``encode``/``decode`` here cover one message at a time;
``decode_stream`` splits a buffer of newline-delimited messages.
"""

from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Optional

from service.sources.base.source import AvInfo

# ---------------------------------------------------------------------------
# Control-channel message types
# ---------------------------------------------------------------------------

# UI -> service requests.
MSG_BROWSE = "browse"
MSG_PLAY = "play"
MSG_PAUSE = "pause"
MSG_RESUME = "resume"
MSG_SEEK = "seek"
MSG_STOP = "stop"
MSG_STATUS = "status"

# service -> UI replies (not part of the prompt's required set, but the schema
# needs a way to answer; kept minimal and explicit).
MSG_CATALOG = "catalog"  # reply to browse
MSG_NOWPLAYING = "nowplaying"  # reply to play / transport
MSG_HEALTH = "health"  # reply to status
MSG_ERROR = "error"

#: The control messages a client (UI) may send. Used for validation.
REQUEST_TYPES = frozenset(
    {
        MSG_BROWSE,
        MSG_PLAY,
        MSG_PAUSE,
        MSG_RESUME,
        MSG_SEEK,
        MSG_STOP,
        MSG_STATUS,
    }
)

#: Required payload fields per request type. Anything not listed takes no
#: required fields. ``play`` requires ``id`` (per service-design §2); ``seek``
#: requires ``t``. ``play`` optionally also carries ``source`` (catalog-browse
#: §7) but ``id`` is source-scoped so it is sufficient on its own.
_REQUIRED_FIELDS: dict[str, tuple[str, ...]] = {
    MSG_PLAY: ("id",),
    MSG_SEEK: ("t",),
}


class ProtocolError(ValueError):
    """Raised when a message cannot be encoded/decoded against the schema."""


def make(type_: str, **fields: Any) -> dict[str, Any]:
    """Build a message dict ``{"type": type_, **fields}`` with validation.

    Validates that requests carry their required fields, so malformed control
    messages fail fast at the boundary rather than deep in the session manager.
    """
    if not isinstance(type_, str) or not type_:
        raise ProtocolError("message type must be a non-empty string")
    msg: dict[str, Any] = {"type": type_}
    msg.update(fields)
    _validate(msg)
    return msg


def _validate(msg: dict[str, Any]) -> None:
    type_ = msg.get("type")
    if not isinstance(type_, str) or not type_:
        raise ProtocolError("message missing string 'type'")
    if type_ in REQUEST_TYPES:
        for required in _REQUIRED_FIELDS.get(type_, ()):
            if required not in msg:
                raise ProtocolError(
                    f"control message {type_!r} requires field {required!r}"
                )
        # seek 't' must be a number (seconds).
        if type_ == MSG_SEEK and not isinstance(msg["t"], (int, float)):
            raise ProtocolError("seek 't' must be a number (seconds)")
        if type_ == MSG_SEEK and isinstance(msg["t"], bool):
            raise ProtocolError("seek 't' must be a number, not a bool")


def encode(msg: dict[str, Any]) -> bytes:
    """Encode one control message to a newline-terminated UTF-8 JSON line."""
    _validate(msg)
    # sort_keys for stable, testable output; separators trim whitespace.
    line = json.dumps(msg, separators=(",", ":"), sort_keys=True)
    return line.encode("utf-8") + b"\n"


def decode(data: bytes) -> dict[str, Any]:
    """Decode a single message (one JSON object, optional trailing newline)."""
    if not isinstance(data, (bytes, bytearray)):
        raise ProtocolError("decode expects bytes")
    text = data.decode("utf-8").strip()
    if not text:
        raise ProtocolError("empty message")
    try:
        msg = json.loads(text)
    except json.JSONDecodeError as exc:  # pragma: no cover - exercised in tests
        raise ProtocolError(f"invalid JSON: {exc}") from exc
    if not isinstance(msg, dict):
        raise ProtocolError("message must be a JSON object")
    _validate(msg)
    return msg


def decode_stream(buf: bytes) -> tuple[list[dict[str, Any]], bytes]:
    """Split a buffer of newline-delimited messages.

    Returns ``(messages, remainder)`` where ``remainder`` is any trailing
    partial line (no terminating newline yet) to be prepended to the next read.
    This is the helper a real TCP control loop uses to frame JSON Lines.
    """
    messages: list[dict[str, Any]] = []
    *complete, remainder = buf.split(b"\n")
    for line in complete:
        if line.strip():
            messages.append(decode(line))
    return messages, remainder


# ---------------------------------------------------------------------------
# Media-socket session preamble (transport.md §2)
# ---------------------------------------------------------------------------


def make_preamble(
    stream_id: str,
    av: AvInfo,
    duration_s: Optional[float],
    nav_available: bool,
) -> dict[str, Any]:
    """Build the one-time media-socket session preamble.

    Sent once on the media socket at ``open``, immediately before the raw PS
    byte stream begins. Carries the stream id, the ``AvInfo``, the duration,
    and whether a nav (GOP/VOBU) seek index is available — exactly the set
    named in ``transport.md`` §2.
    """
    return {
        "type": "session",
        "stream_id": stream_id,
        "av": asdict(av),
        "duration_s": duration_s,
        "nav_available": bool(nav_available),
    }


def encode_preamble(preamble: dict[str, Any]) -> bytes:
    """Encode the preamble as a single newline-terminated JSON line.

    The newline cleanly separates the JSON preamble from the raw PS bytes that
    follow on the same socket, so a consumer reads one line then treats the
    rest as a self-delimiting Program Stream.
    """
    if preamble.get("type") != "session":
        raise ProtocolError("preamble must have type 'session'")
    line = json.dumps(preamble, separators=(",", ":"), sort_keys=True)
    return line.encode("utf-8") + b"\n"


def decode_preamble(data: bytes) -> dict[str, Any]:
    """Decode a session preamble line (the part before the raw PS bytes)."""
    msg = json.loads(data.decode("utf-8").strip())
    if not isinstance(msg, dict) or msg.get("type") != "session":
        raise ProtocolError("not a session preamble")
    return msg
