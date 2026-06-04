"""PS-over-TCP media server + JSON control channel (transport.md §2).

This is the **M2 "stub Pi server"**, but built on the *real* ``Source`` /
``Streamer`` / ``protocol`` layers rather than a throwaway — only the network
plumbing is new. It runs **off-target on localhost loopback** (no Pi, no real
LAN hardware); the same socket logic is what a Pi deployment uses.

Topology (two TCP listeners, per ``transport.md`` §2's "separate control
channel"):

* **Control channel** — newline-delimited JSON (``protocol.py``). One client
  connection per console. Carries ``browse`` / ``play{id}`` / ``pause`` /
  ``resume`` / ``seek{t}`` / ``stop`` / ``status`` and the service's replies
  (``catalog`` / ``nowplaying`` / ``health`` / ``error``).
* **Media channel** — a raw MPEG-2 Program Stream. On ``play`` the control
  channel returns a ``nowplaying`` reply carrying a ``session_id`` and the
  ``media_port``; the client opens the media socket and sends that
  ``session_id`` as a single JSON line to claim its stream. The server then
  writes the **one-time session preamble** (``protocol.make_preamble``) and
  pumps PS bytes from the source's ``StreamHandle`` through a ``Streamer``.

**Pacing / backpressure (transport.md §4).** The ``Streamer``'s injected
*writer* is the media socket's ``sendall``. With no ``rate_bytes_per_s`` cap,
the streamer pulls and writes as fast as the socket accepts; once the OS send
buffer fills (because the consumer/ARM stops reading), ``sendall`` blocks — TCP
flow control is the real pacer, exactly as the doc specifies. ``pause`` parks
the streamer thread on a condition (no bytes flow); ``resume`` wakes it;
``stop`` ends the session and closes the media socket cleanly.

Threading model: one acceptor thread per listener; one handler thread per
control connection; one streamer thread per active session. ``stdlib`` only.
"""

from __future__ import annotations

import socket
import threading
import uuid
from dataclasses import asdict
from typing import Any, Optional

from service.core import protocol as proto
from service.core.catalog import Catalog
from service.core.streamer import State, Streamer
from service.sources.base.source import AvInfo, Source, StreamHandle


# A control connection's recv chunk and the media writer chunk. The media
# chunk is intentionally small-ish so an unread consumer fills the OS buffer
# (and thus backpressures) at a fine granularity in tests.
_CTRL_RECV = 4096


class _SocketWriter:
    """Adapts a connected socket to the ``Streamer``'s ``write(bytes) -> int``.

    ``sendall`` is used so the full chunk is delivered (or it raises); it
    blocks when the OS send buffer is full, which is the TCP backpressure the
    transport design relies on as the real pacer.
    """

    def __init__(self, sock: socket.socket) -> None:
        self._sock = sock

    def write(self, data: bytes) -> int:
        self._sock.sendall(data)
        return len(data)


class _Session:
    """One play session: a ``StreamHandle`` + its ``Streamer`` + threading.

    The streamer runs on its own thread. ``pause``/``resume`` gate that thread
    via a ``threading.Event`` so a paused session blocks (no busy spin, no byte
    flow); ``seek`` and ``stop`` are forwarded to the streamer. The session is
    *armed* on ``play`` (so its id is known to the control client) and *bound*
    when the media socket claims it.
    """

    def __init__(
        self,
        session_id: str,
        source: Source,
        media_id: str,
        *,
        chunk_size: int,
        prebuffer_bytes: int,
        rate_bytes_per_s: Optional[float],
    ) -> None:
        self.session_id = session_id
        self.source = source
        self.media_id = media_id
        self._chunk_size = chunk_size
        self._prebuffer_bytes = prebuffer_bytes
        self._rate_bytes_per_s = rate_bytes_per_s

        self.handle: Optional[StreamHandle] = None
        self.streamer: Optional[Streamer] = None
        self._media_sock: Optional[socket.socket] = None
        self._thread: Optional[threading.Thread] = None

        # Gate the streamer thread for pause/resume. Set => may run.
        self._run_gate = threading.Event()
        self._run_gate.set()
        self._lock = threading.Lock()
        self._closed = False
        self._paused = False
        # Set once the handle has been opened (so the preamble is buildable).
        self.opened = threading.Event()

    # -- lifecycle ----------------------------------------------------------

    def open(self) -> StreamHandle:
        """Open the source handle (called when arming the session)."""
        self.handle = self.source.open(self.media_id)
        self.opened.set()
        return self.handle

    def preamble(self) -> dict[str, Any]:
        """Build the one-time media-socket session preamble for this handle."""
        assert self.handle is not None
        av: AvInfo = self.handle.av
        return proto.make_preamble(
            stream_id=self.session_id,
            av=av,
            duration_s=self.handle.duration_s,
            nav_available=bool(self.handle.nav and self.handle.nav.entries),
        )

    def bind_media(self, media_sock: socket.socket) -> None:
        """Attach the claimed media socket and start the streamer thread."""
        with self._lock:
            if self._closed:
                # stop() already ran (e.g. control channel dropped); refuse to
                # start a doomed pump and let the caller close the socket.
                raise OSError("session already stopped")
            assert self.handle is not None
            self._media_sock = media_sock
            writer = _SocketWriter(media_sock)
            self.streamer = Streamer(
                self.handle,
                writer,
                chunk_size=self._chunk_size,
                prebuffer_bytes=self._prebuffer_bytes,
                rate_bytes_per_s=self._rate_bytes_per_s,
                # Last-moment gate: never emit a chunk while paused/stopped,
                # even if it was pulled before the pause landed (closes the
                # read-then-pause race so pause deterministically stops flow).
                can_emit=self._run_gate.is_set,
            )
            thread = threading.Thread(
                target=self._pump,
                name=f"streamer-{self.session_id}",
                daemon=True,
            )
            # Start while holding the lock, then publish the reference, so
            # stop() never observes a thread object that hasn't been started.
            thread.start()
            self._thread = thread

    def _pump(self) -> None:
        """Streamer pump loop, gated by pause/resume; ends on stop/EOF/error."""
        s = self.streamer
        assert s is not None
        try:
            while True:
                # Block here while paused (gate cleared). Re-checks after wake.
                self._run_gate.wait()
                with self._lock:
                    if self._closed or s.state in (State.STOPPED, State.DONE):
                        break
                    if s.state is State.PAUSED:
                        # A pause raced in after the gate; loop and wait again.
                        continue
                # One unit of work outside the lock so sendall can block on
                # backpressure without holding the control lock.
                if not s.step():
                    # step() returns False on DONE/STOPPED/PAUSED.
                    if s.state is State.PAUSED:
                        continue
                    break
        except OSError:
            # Media socket closed under us (client/stop). Treat as end.
            pass
        finally:
            self._finish_media_socket()

    def _finish_media_socket(self) -> None:
        sock = self._media_sock
        self._media_sock = None
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_WR)
            except OSError:
                pass
            try:
                sock.close()
            except OSError:
                pass

    # -- control surface ----------------------------------------------------

    def pause(self) -> None:
        with self._lock:
            self._paused = True
            if self.streamer is not None:
                self.streamer.pause()
        # Clear the gate AFTER setting PAUSED so the pump parks.
        self._run_gate.clear()

    def resume(self) -> None:
        with self._lock:
            self._paused = False
            if self.streamer is not None:
                self.streamer.resume()
        self._run_gate.set()

    def seek(self, t_seconds: float) -> None:
        with self._lock:
            if self.streamer is not None:
                self.streamer.seek(t_seconds)
                # Streamer.seek() forces BUFFERING; if the session was paused,
                # keep it paused so byte flow stays stopped until resume.
                if self._paused:
                    self.streamer.pause()
        # Only release the gate if we are NOT paused (a seek mid-play keeps
        # playing; a seek while paused stays parked until resume).
        if not self._paused:
            self._run_gate.set()

    def stop(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            if self.streamer is not None:
                self.streamer.stop()
            elif self.handle is not None:
                self.handle.close()
        # Wake the pump so it observes STOPPED and exits.
        self._run_gate.set()
        thread = self._thread
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5.0)
        self._finish_media_socket()

    def status(self) -> dict[str, Any]:
        with self._lock:
            state = self.streamer.state.value if self.streamer else "armed"
            written = self.streamer.bytes_written if self.streamer else 0
        return {
            "session_id": self.session_id,
            "id": self.media_id,
            "state": state,
            "bytes_written": written,
        }


class Server:
    """A loopback PS media server + JSON control server over a ``Catalog``.

    Parameters
    ----------
    catalog:
        The ``Catalog`` of registered ``Source`` backends.
    host:
        Bind address. Defaults to loopback (off-target only).
    control_port / media_port:
        ``0`` => let the OS pick an ephemeral port (read back via
        ``control_address`` / ``media_address`` after ``start``). The chosen
        ``media_port`` is also handed to clients inside the ``nowplaying``
        reply so they know where to open the media socket.
    chunk_size / prebuffer_bytes / rate_bytes_per_s:
        Forwarded to each session's ``Streamer``. ``rate_bytes_per_s`` defaults
        to ``None`` (no software cap — TCP flow control paces, per §4).
    """

    def __init__(
        self,
        catalog: Catalog,
        *,
        host: str = "127.0.0.1",
        control_port: int = 0,
        media_port: int = 0,
        chunk_size: int = 32 * 1024,
        prebuffer_bytes: int = 64 * 1024,
        rate_bytes_per_s: Optional[float] = None,
    ) -> None:
        self.catalog = catalog
        self.host = host
        self._chunk_size = chunk_size
        self._prebuffer_bytes = prebuffer_bytes
        self._rate_bytes_per_s = rate_bytes_per_s

        self._ctrl_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._ctrl_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._media_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._media_sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._ctrl_sock.bind((host, control_port))
        self._media_sock.bind((host, media_port))

        # Sessions, keyed by session_id. Armed on play; claimed on media
        # connect; removed on stop. Guarded by _sessions_lock.
        self._sessions: dict[str, _Session] = {}
        self._sessions_lock = threading.Lock()

        self._threads: list[threading.Thread] = []
        self._running = False
        #: Set once *both* listeners are bound + listening — the real
        #: readiness signal tests wait on (no sleeps).
        self.ready = threading.Event()

    # -- addresses (valid after construction; ports are bound in __init__) --

    @property
    def control_address(self) -> tuple[str, int]:
        return self._ctrl_sock.getsockname()

    @property
    def media_address(self) -> tuple[str, int]:
        return self._media_sock.getsockname()

    @property
    def media_port(self) -> int:
        return self.media_address[1]

    # -- lifecycle ----------------------------------------------------------

    def start(self) -> None:
        """Listen on both sockets and spawn the two acceptor threads."""
        if self._running:
            return
        self._running = True
        self._ctrl_sock.listen(8)
        self._media_sock.listen(8)
        self._ctrl_sock.settimeout(0.5)
        self._media_sock.settimeout(0.5)
        for target, name in (
            (self._accept_control, "accept-control"),
            (self._accept_media, "accept-media"),
        ):
            t = threading.Thread(target=target, name=name, daemon=True)
            t.start()
            self._threads.append(t)
        # Both listeners are now bound + listening.
        self.ready.set()

    def shutdown(self) -> None:
        """Stop accepting, end all sessions, and close the listeners."""
        self._running = False
        with self._sessions_lock:
            sessions = list(self._sessions.values())
            self._sessions.clear()
        for sess in sessions:
            sess.stop()
        for t in self._threads:
            if t is not threading.current_thread():
                t.join(timeout=2.0)
        self._threads.clear()
        for sock in (self._ctrl_sock, self._media_sock):
            try:
                sock.close()
            except OSError:
                pass
        self.ready.clear()

    def __enter__(self) -> "Server":
        self.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self.shutdown()

    # -- acceptors ----------------------------------------------------------

    def _accept_control(self) -> None:
        while self._running:
            try:
                conn, _addr = self._ctrl_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._serve_control, args=(conn,),
                name="ctrl-conn", daemon=True,
            )
            t.start()
            self._threads.append(t)

    def _accept_media(self) -> None:
        while self._running:
            try:
                conn, _addr = self._media_sock.accept()
            except socket.timeout:
                continue
            except OSError:
                break
            t = threading.Thread(
                target=self._serve_media, args=(conn,),
                name="media-conn", daemon=True,
            )
            t.start()
            self._threads.append(t)

    # -- control connection handler -----------------------------------------

    def _serve_control(self, conn: socket.socket) -> None:
        """Read newline-delimited JSON requests; dispatch; reply in kind.

        One *active* session per control connection (the design's "one session
        per connected console"). ``play`` arms a new session; transport
        messages act on it.
        """
        active: Optional[_Session] = None
        buf = b""
        try:
            with conn:
                while self._running:
                    try:
                        data = conn.recv(_CTRL_RECV)
                    except (socket.timeout, BlockingIOError):
                        continue
                    except OSError:
                        break
                    if not data:
                        break  # client closed the control channel
                    buf += data
                    messages, buf = proto.decode_stream(buf)
                    for msg in messages:
                        reply, active = self._dispatch(msg, active)
                        if reply is not None:
                            conn.sendall(proto.encode(reply))
        finally:
            # If the control channel drops with a live session, tear it down.
            if active is not None:
                self._end_session(active)

    def _dispatch(
        self, msg: dict[str, Any], active: Optional[_Session]
    ) -> tuple[Optional[dict[str, Any]], Optional[_Session]]:
        mtype = msg.get("type")
        try:
            if mtype == proto.MSG_BROWSE:
                return self._handle_browse(msg), active
            if mtype == proto.MSG_PLAY:
                return self._handle_play(msg, active)
            if mtype == proto.MSG_PAUSE:
                if active is not None:
                    active.pause()
                return self._nowplaying(active, "paused"), active
            if mtype == proto.MSG_RESUME:
                if active is not None:
                    active.resume()
                return self._nowplaying(active, "playing"), active
            if mtype == proto.MSG_SEEK:
                if active is not None:
                    active.seek(float(msg["t"]))
                return self._nowplaying(active, "seeking", t=msg["t"]), active
            if mtype == proto.MSG_STOP:
                self._end_session(active)
                return proto.make(proto.MSG_NOWPLAYING, state="stopped"), None
            if mtype == proto.MSG_STATUS:
                return self._handle_status(active), active
        except (KeyError, ValueError, FileNotFoundError, NotImplementedError) as exc:
            return proto.make(proto.MSG_ERROR, message=str(exc)), active
        return (
            proto.make(proto.MSG_ERROR, message=f"unknown message {mtype!r}"),
            active,
        )

    # -- request handlers ---------------------------------------------------

    def _handle_browse(self, msg: dict[str, Any]) -> dict[str, Any]:
        path = msg.get("path")
        buckets = self.catalog.browse(path)
        # Keep libraries SEPARATE/labeled: {library_name: [entry-dicts]}.
        libraries = {
            name: [asdict(e) for e in entries]
            for name, entries in buckets.items()
        }
        return proto.make(proto.MSG_CATALOG, libraries=libraries)

    def _handle_play(
        self, msg: dict[str, Any], active: Optional[_Session]
    ) -> tuple[dict[str, Any], _Session]:
        media_id = msg["id"]
        # End any prior session on this control connection first.
        self._end_session(active)

        source = self._resolve_source(media_id, msg.get("source"))
        if source is None:
            raise ValueError(f"no source for id {media_id!r}")

        session_id = uuid.uuid4().hex
        sess = _Session(
            session_id,
            source,
            media_id,
            chunk_size=self._chunk_size,
            prebuffer_bytes=self._prebuffer_bytes,
            rate_bytes_per_s=self._rate_bytes_per_s,
        )
        # open() may raise (bad id / not implemented) -> surfaces as error.
        sess.open()
        with self._sessions_lock:
            self._sessions[session_id] = sess

        reply = proto.make(
            proto.MSG_NOWPLAYING,
            state="armed",
            session_id=session_id,
            id=media_id,
            media_host=self.host,
            media_port=self.media_port,
        )
        return reply, sess

    def _handle_status(
        self, active: Optional[_Session]
    ) -> dict[str, Any]:
        return proto.make(
            proto.MSG_HEALTH,
            libraries=self.catalog.health(),
            session=active.status() if active is not None else None,
        )

    def _nowplaying(
        self, active: Optional[_Session], state: str, **extra: Any
    ) -> dict[str, Any]:
        if active is None:
            return proto.make(
                proto.MSG_ERROR, message="no active session"
            )
        fields: dict[str, Any] = {
            "state": state,
            "session_id": active.session_id,
            "id": active.media_id,
        }
        fields.update(extra)
        return proto.make(proto.MSG_NOWPLAYING, **fields)

    def _resolve_source(
        self, media_id: str, source_name: Optional[str]
    ) -> Optional[Source]:
        """Pick the source for ``media_id``.

        Explicit ``source`` (library name) wins; else match the ``"<src>:"``
        id prefix against each source's id namespace; else fall back to the
        sole source if there is exactly one.
        """
        if source_name:
            return self.catalog.find_source(source_name)
        # id prefix convention: "dvddump:...", "plex:...".
        prefix = media_id.split(":", 1)[0] if ":" in media_id else ""
        for src in self.catalog.sources:
            # Match by lowercased, space-stripped library name OR by the
            # prefix a source uses (dvddump/plex). Heuristic, off-target only.
            squashed = src.name.lower().replace(" ", "")
            if prefix and (prefix in squashed or squashed.startswith(prefix)):
                return src
        sources = self.catalog.sources
        if len(sources) == 1:
            return sources[0]
        # Last resort: prefix-based well-known mapping.
        well_known = {"dvddump": "DVD Dumps", "plex": "Plex"}
        if prefix in well_known:
            return self.catalog.find_source(well_known[prefix])
        return None

    def _end_session(self, active: Optional[_Session]) -> None:
        if active is None:
            return
        with self._sessions_lock:
            self._sessions.pop(active.session_id, None)
        active.stop()

    # -- media connection handler -------------------------------------------

    def _serve_media(self, conn: socket.socket) -> None:
        """A media client claims its armed session by sending one JSON line.

        Expected first line: ``{"type":"claim","session_id":"<hex>"}`` (or just
        ``{"session_id": ...}``). We then write the preamble and hand the
        socket to the session's streamer. If the session is unknown the media
        socket is closed.
        """
        buf = b""
        sess: Optional[_Session] = None
        conn.settimeout(5.0)
        try:
            # Read exactly one newline-delimited JSON line to identify session.
            while b"\n" not in buf:
                try:
                    data = conn.recv(_CTRL_RECV)
                except socket.timeout:
                    conn.close()
                    return
                if not data:
                    conn.close()
                    return
                buf += data
            line, _, _ = buf.partition(b"\n")
            claim = proto.decode(line)
            session_id = claim.get("session_id")
            with self._sessions_lock:
                sess = self._sessions.get(session_id) if session_id else None
            if sess is None or sess.handle is None:
                conn.close()
                return
            # Write the one-time preamble, then hand off to the streamer.
            conn.settimeout(None)
            conn.sendall(proto.encode_preamble(sess.preamble()))
            sess.bind_media(conn)
            # Ownership of the socket transfers to the session's pump thread.
        except (OSError, proto.ProtocolError):
            try:
                conn.close()
            except OSError:
                pass
