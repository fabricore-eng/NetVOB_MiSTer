"""End-to-end loopback integration test for the PS-over-TCP server.

Real sockets, real threads, real ``Streamer`` / ``protocol`` / ``Source``.
Binds ``127.0.0.1:0`` (OS-picked ports), runs the server in a background
thread, and drives a client over the JSON control channel through the full
transport lifecycle: ``browse`` -> ``play`` -> (claim media) -> receive ->
``pause`` -> ``resume`` -> ``seek`` -> ``stop``.

No flakiness by construction:

* Readiness is the server's ``ready`` ``threading.Event`` (set after *both*
  listeners bind+listen), never a sleep.
* The pause/resume byte-flow gating uses a ``GatedSource`` whose ``read()``
  blocks on a test-controlled ``Event``, so "pause stops flow / resume
  continues" is observed against deterministic, test-owned byte availability
  rather than timing.
* Byte-for-byte equality is asserted against (a) a ``FakeSource`` with known
  PS bytes and (b) the real ``DVDDumpSource`` over a crafted in-memory PS.
"""

import service.tests._bootstrap  # noqa: F401

import json
import socket
import threading
import unittest

from service.core import protocol as proto
from service.core.catalog import Catalog
from service.core.server import Server
from service.sources.base.source import (
    AvInfo,
    CatalogEntry,
    NavInfo,
    Source,
    StreamHandle,
)
from service.sources.dvddump.dvddump import DVDDumpSource


PREFIX = b"\x00\x00\x01"


def _pes(sid: int, payload: bytes) -> bytes:
    """A length-prefixed PES packet: 00 00 01 sid LL LL <payload>."""
    return PREFIX + bytes([sid]) + len(payload).to_bytes(2, "big") + payload


# ---------------------------------------------------------------------------
# Test sources
# ---------------------------------------------------------------------------


class _MemHandle(StreamHandle):
    """In-memory PS handle over a known byte string (FakeSource backing)."""

    def __init__(self, data: bytes, *, nav=None, duration_s=300.0, av=None):
        self._data = data
        self._pos = 0
        self.duration_s = duration_s
        self.nav = nav
        self.av = av or AvInfo(audio="ac3")
        self.closed = False

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        chunk = self._data[self._pos : self._pos + n]
        self._pos += len(chunk)
        return chunk

    def seek(self, t_seconds: float) -> None:
        if self.nav is not None:
            target = self.nav.nearest_preceding(t_seconds)
            self._pos = target[1] if target is not None else 0
        else:
            self._pos = 0

    def close(self) -> None:
        self.closed = True


class FakeSource(Source):
    """A Source yielding known PS bytes for a fixed id. No disk, no network."""

    name = "Fake"
    badge = "field-exact"
    is_local_to_console = False

    def __init__(self, ps_bytes: bytes, *, nav=None, av=None):
        self._data = ps_bytes
        self._nav = nav
        self._av = av
        self.opened_ids: list[str] = []

    def browse(self, path=None):
        return [
            CatalogEntry(
                id="fake:clip",
                title="Known Clip",
                kind="title",
                duration_s=300.0,
                badge=self.badge,
            )
        ]

    def open(self, id: str) -> StreamHandle:
        self.opened_ids.append(id)
        return _MemHandle(self._data, nav=self._nav, av=self._av)

    def health(self) -> dict:
        return {"name": self.name, "reachable": True, "configured": True}


class _GatedHandle(StreamHandle):
    """A handle whose ``read`` returns chunks only as a gate is opened.

    ``feed(k)`` makes ``k`` more bytes available; ``read`` blocks until at
    least one byte is available (or EOF is declared). This lets the test
    deterministically decide when bytes can flow, so pause/resume assertions
    don't depend on timing.
    """

    CHUNK = 4096  # bytes released per feed unit

    def __init__(self, data: bytes, *, nav=None):
        self._data = data
        self._pos = 0
        self._available = 0  # absolute byte index released for reading
        self._eof = False
        self._cv = threading.Condition()
        self.duration_s = 300.0
        self.nav = nav
        self.av = AvInfo(audio="ac3")
        self.closed = False

    def feed(self, nbytes: int) -> None:
        with self._cv:
            self._available = min(len(self._data), self._available + nbytes)
            self._cv.notify_all()

    def feed_rest(self) -> None:
        with self._cv:
            self._available = len(self._data)
            self._eof = True
            self._cv.notify_all()

    def read(self, n: int) -> bytes:
        if n <= 0:
            return b""
        with self._cv:
            while True:
                ready = self._available - self._pos
                if ready > 0:
                    take = min(n, ready)
                    chunk = self._data[self._pos : self._pos + take]
                    self._pos += take
                    return chunk
                if self._eof or self.closed:
                    return b""  # EOF
                self._cv.wait()

    def seek(self, t_seconds: float) -> None:
        with self._cv:
            if self.nav is not None:
                target = self.nav.nearest_preceding(t_seconds)
                self._pos = target[1] if target is not None else 0
            else:
                self._pos = 0
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify_all()


class GatedSource(Source):
    name = "Gated"
    badge = "field-exact"
    is_local_to_console = False

    def __init__(self, data: bytes, *, nav=None):
        self._data = data
        self._nav = nav
        self.handle: _GatedHandle | None = None

    def browse(self, path=None):
        return [CatalogEntry(id="gated:clip", title="Gated", kind="title")]

    def open(self, id: str) -> StreamHandle:
        self.handle = _GatedHandle(self._data, nav=self._nav)
        return self.handle

    def health(self) -> dict:
        return {"name": self.name, "reachable": True, "configured": True}


# ---------------------------------------------------------------------------
# Client helper (one control socket + one media socket)
# ---------------------------------------------------------------------------


class _Client:
    """A loopback client: a control connection + (on play) a media connection."""

    def __init__(self, server: Server):
        self.server = server
        self.ctrl = socket.create_connection(server.control_address, timeout=5.0)
        self.ctrl.settimeout(5.0)
        self._ctrl_buf = b""
        self.media: socket.socket | None = None
        self._media_buf = b""

    # -- control channel ----------------------------------------------------

    def send(self, msg: dict) -> None:
        self.ctrl.sendall(proto.encode(msg))

    def recv_msg(self) -> dict:
        """Read exactly one newline-delimited JSON reply."""
        while b"\n" not in self._ctrl_buf:
            data = self.ctrl.recv(4096)
            if not data:
                raise EOFError("control channel closed")
            self._ctrl_buf += data
        line, _, self._ctrl_buf = self._ctrl_buf.partition(b"\n")
        return json.loads(line.decode("utf-8"))

    # -- media channel ------------------------------------------------------

    def open_media(self, session_id: str) -> dict:
        """Connect the media socket, claim the session, return the preamble."""
        self.media = socket.create_connection(
            self.server.media_address, timeout=5.0
        )
        self.media.settimeout(5.0)
        # Claim the armed session with one newline-delimited JSON line, then
        # read the one-time preamble that precedes the raw PS bytes.
        claim = json.dumps(
            {"type": "claim", "session_id": session_id}
        ).encode("utf-8") + b"\n"
        self.media.sendall(claim)
        return self._read_preamble()

    def _read_preamble(self) -> dict:
        while b"\n" not in self._media_buf:
            data = self.media.recv(4096)
            if not data:
                raise EOFError("media channel closed before preamble")
            self._media_buf += data
        line, _, self._media_buf = self._media_buf.partition(b"\n")
        return proto.decode_preamble(line)

    def media_recv(self, nbytes: int) -> bytes:
        """Receive exactly ``nbytes`` of PS payload (after the preamble)."""
        while len(self._media_buf) < nbytes:
            data = self.media.recv(65536)
            if not data:
                break
            self._media_buf += data
        out = self._media_buf[:nbytes]
        self._media_buf = self._media_buf[nbytes:]
        return out

    def media_recv_all(self) -> bytes:
        """Drain the media socket to EOF; return all PS payload bytes."""
        out = bytearray(self._media_buf)
        self._media_buf = b""
        while True:
            data = self.media.recv(65536)
            if not data:
                break
            out += data
        return bytes(out)

    def media_buffered(self) -> int:
        return len(self._media_buf)

    def close(self) -> None:
        for s in (self.media, self.ctrl):
            if s is not None:
                try:
                    s.close()
                except OSError:
                    pass


def _build_ps(num_video_packets: int = 64, payload_size: int = 1024) -> bytes:
    """Craft a deterministic in-memory MPEG-2 PS with a nav packet to strip.

    Layout: [pack-ish video PES]* with one 0xBF nav PES near the front and a
    program-end code at the tail. Used both directly (FakeSource) and through
    DVDDumpSource (which strips the 0xBF packet).
    """
    parts = []
    for i in range(num_video_packets):
        payload = bytes([(i + j) & 0xFF for j in range(payload_size)])
        parts.append(_pes(0xE0, payload))
        if i == 2:
            parts.append(_pes(0xBF, b"NAV-PCI-DSI-DROP-ME" * 4))
    parts.append(PREFIX + b"\xb9")  # MPEG_program_end_code
    return b"".join(parts)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class ServerLoopbackTest(unittest.TestCase):
    def _start_server(self, source: Source, **kw) -> Server:
        cat = Catalog([source])
        server = Server(cat, host="127.0.0.1", **kw)
        server.start()
        # Real readiness signal — both listeners bound+listening. No sleep.
        self.assertTrue(server.ready.wait(timeout=5.0))
        self.addCleanup(server.shutdown)
        return server

    # -- browse -------------------------------------------------------------

    def test_browse_returns_labeled_libraries(self):
        ps = _build_ps()
        server = self._start_server(FakeSource(ps))
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_BROWSE))
        reply = client.recv_msg()
        self.assertEqual(reply["type"], proto.MSG_CATALOG)
        # Libraries stay SEPARATE/labeled by source name.
        self.assertIn("Fake", reply["libraries"])
        self.assertEqual(len(reply["libraries"]["Fake"]), 1)
        self.assertEqual(reply["libraries"]["Fake"][0]["id"], "fake:clip")
        self.assertEqual(
            reply["libraries"]["Fake"][0]["title"], "Known Clip"
        )

    # -- play + byte-for-byte delivery (FakeSource) -------------------------

    def test_play_streams_source_ps_byte_for_byte(self):
        ps = _build_ps(num_video_packets=80, payload_size=2048)
        src = FakeSource(ps, av=AvInfo(audio="ac3", field_cadence="interlaced"))
        server = self._start_server(
            src, chunk_size=8192, prebuffer_bytes=16384
        )
        client = _Client(server)
        self.addCleanup(client.close)

        # play -> armed reply with session id + media port.
        client.send(proto.make(proto.MSG_PLAY, id="fake:clip"))
        reply = client.recv_msg()
        self.assertEqual(reply["type"], proto.MSG_NOWPLAYING)
        self.assertEqual(reply["state"], "armed")
        self.assertEqual(reply["id"], "fake:clip")
        session_id = reply["session_id"]
        self.assertTrue(session_id)
        self.assertEqual(reply["media_port"], server.media_port)

        # Open media socket, claim the session, read the preamble.
        preamble = client.open_media(session_id)
        self.assertEqual(preamble["type"], "session")
        self.assertEqual(preamble["stream_id"], session_id)
        self.assertEqual(preamble["av"]["video"], "mpeg2")
        self.assertEqual(preamble["av"]["audio"], "ac3")
        self.assertEqual(preamble["av"]["field_cadence"], "interlaced")
        self.assertEqual(preamble["duration_s"], 300.0)
        self.assertFalse(preamble["nav_available"])  # FakeSource has no nav

        # The media payload equals the source PS, byte-for-byte.
        received = client.media_recv_all()
        self.assertEqual(received, ps)
        self.assertEqual(len(received), len(ps))

    # -- play over the REAL DVDDumpSource (nav-strip applied) ---------------

    def test_play_real_dvddump_source_strips_nav_byte_for_byte(self):
        raw = _build_ps(num_video_packets=40, payload_size=1500)
        # DVDDumpSource.open() strips the 0xBF nav packet; the server must
        # deliver exactly that stripped stream.
        from service.sources.dvddump.dvddump import strip_nav_packets

        expected = strip_nav_packets(raw)
        self.assertNotEqual(expected, raw)  # a nav packet was actually present

        # Wire DVDDumpSource to serve our crafted bytes via a tiny shim that
        # routes open() through open_bytes() with stripping.
        class _MemDVD(DVDDumpSource):
            name = "DVD Dumps"

            def open(self, id: str):
                return self.open_bytes(raw, already_stripped=False)

            def browse(self, path=None):
                return [
                    CatalogEntry(
                        id="dvddump:clip", title="Clip", kind="title",
                        badge="field-exact",
                    )
                ]

        server = self._start_server(
            _MemDVD(root=None), chunk_size=4096, prebuffer_bytes=8192
        )
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_PLAY, id="dvddump:clip"))
        reply = client.recv_msg()
        session_id = reply["session_id"]
        preamble = client.open_media(session_id)
        self.assertEqual(preamble["av"]["audio"], "ac3")

        received = client.media_recv_all()
        self.assertEqual(received, expected)
        # And the nav payload is gone.
        self.assertNotIn(b"NAV-PCI-DSI-DROP-ME", received)

    # -- preamble nav flag set when the source HAS a nav index --------------

    def test_preamble_reports_nav_available(self):
        ps = _build_ps()
        nav = NavInfo(entries=[(0.0, 0), (5.0, 4096)])
        server = self._start_server(FakeSource(ps, nav=nav))
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_PLAY, id="fake:clip"))
        session_id = client.recv_msg()["session_id"]
        preamble = client.open_media(session_id)
        self.assertTrue(preamble["nav_available"])

    # -- pause stops byte flow; resume continues; delivery is exact ---------

    def test_pause_stops_flow_resume_continues(self):
        total = _GatedHandle.CHUNK * 8  # 32 KiB
        data = bytes([i & 0xFF for i in range(total)])
        src = GatedSource(data)
        # prebuffer 0 so the streamer doesn't pre-pull past the gate; small
        # chunk so flow is observable in steps.
        server = self._start_server(
            src, chunk_size=_GatedHandle.CHUNK, prebuffer_bytes=0
        )
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_PLAY, id="gated:clip"))
        session_id = client.recv_msg()["session_id"]
        preamble = client.open_media(session_id)
        self.assertEqual(preamble["stream_id"], session_id)

        gated = src.handle
        self.assertIsNotNone(gated)

        # Release the first half, receive it exactly.
        first_half = total // 2
        gated.feed(first_half)
        got_first = client.media_recv(first_half)
        self.assertEqual(got_first, data[:first_half])

        # Pause. Now even if we release more bytes from the source, none must
        # reach the client (the streamer is parked). We verify by releasing
        # the rest, then confirming a short recv times out (no flow).
        client.send(proto.make(proto.MSG_PAUSE))
        pause_reply = client.recv_msg()
        self.assertEqual(pause_reply["state"], "paused")

        gated.feed_rest()  # source now has everything available
        client.media.settimeout(0.4)
        with self.assertRaises(socket.timeout):
            # No bytes should arrive while paused.
            client.media.recv(4096)
        self.assertEqual(client.media_buffered(), 0)

        # Resume. The remaining bytes now flow and complete the stream.
        client.media.settimeout(5.0)
        client.send(proto.make(proto.MSG_RESUME))
        resume_reply = client.recv_msg()
        self.assertEqual(resume_reply["state"], "playing")

        rest = client.media_recv_all()
        # Full payload received exactly, in order, across the pause boundary.
        self.assertEqual(got_first + rest, data)

    # -- seek repositions the source (delivers from the seek point) ---------

    def test_seek_repositions_stream(self):
        # 16 KiB of distinct bytes; nav index puts t=5s at offset 4096. A
        # GatedSource releases NO bytes until we explicitly feed, so the
        # streamer is parked in read() (nothing emitted yet) when the seek
        # lands -> the seek deterministically wins, with no offset-0 leakage.
        total = 16384
        data = bytes([i & 0xFF for i in range(total)])
        nav = NavInfo(entries=[(0.0, 0), (5.0, 4096), (10.0, 8192)])
        src = GatedSource(data, nav=nav)
        server = self._start_server(
            src, chunk_size=2048, prebuffer_bytes=0, rate_bytes_per_s=None
        )
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_PLAY, id="gated:clip"))
        session_id = client.recv_msg()["session_id"]
        preamble = client.open_media(session_id)
        self.assertTrue(preamble["nav_available"])  # nav index present

        # Pause first so the streamer's emit gate is provably closed; this
        # removes any timing dependency on whether the pump has reached its
        # (blocked) read() yet -- nothing can be emitted while paused.
        client.send(proto.make(proto.MSG_PAUSE))
        self.assertEqual(client.recv_msg()["state"], "paused")

        # Seek to ~7.5s -> source repositions to offset 4096 (nearest
        # preceding) and flushes any pre-seek buffer.
        client.send(proto.make(proto.MSG_SEEK, t=7.5))
        seek_reply = client.recv_msg()
        self.assertEqual(seek_reply["state"], "seeking")
        self.assertEqual(seek_reply["t"], 7.5)

        # Release everything and resume; the stream flows from the seek offset.
        src.handle.feed_rest()
        client.send(proto.make(proto.MSG_RESUME))
        self.assertEqual(client.recv_msg()["state"], "playing")

        received = client.media_recv_all()
        # Everything from the seek offset to EOF, byte-for-byte.
        self.assertEqual(received, data[4096:])

    # -- stop closes the media socket cleanly -------------------------------

    def test_stop_closes_cleanly(self):
        ps = _build_ps(num_video_packets=200, payload_size=4096)  # ~800 KiB
        src = GatedSource(ps)
        server = self._start_server(
            src, chunk_size=_GatedHandle.CHUNK, prebuffer_bytes=0
        )
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_PLAY, id="gated:clip"))
        session_id = client.recv_msg()["session_id"]
        client.open_media(session_id)

        gated = src.handle
        # Release a little so the stream is live, read some.
        gated.feed(_GatedHandle.CHUNK * 2)
        chunk = client.media_recv(_GatedHandle.CHUNK)  # exact
        self.assertEqual(len(chunk), _GatedHandle.CHUNK)

        # Stop. The control reply confirms stopped; the media socket then
        # reaches a clean EOF (recv returns b"") rather than erroring.
        client.send(proto.make(proto.MSG_STOP))
        stop_reply = client.recv_msg()
        self.assertEqual(stop_reply["state"], "stopped")

        # The source handle was closed by stop().
        self.assertTrue(gated.closed)

        # Media socket: drain to a clean EOF (no exception). recv() must
        # eventually return b"" because the server shut the write side.
        client.media.settimeout(5.0)
        got_eof = False
        while True:
            data = client.media.recv(65536)
            if not data:
                got_eof = True
                break
        self.assertTrue(got_eof)

    # -- status reports library health + session state ----------------------

    def test_status_reports_health_and_session(self):
        ps = _build_ps()
        server = self._start_server(FakeSource(ps))
        client = _Client(server)
        self.addCleanup(client.close)

        # No session yet.
        client.send(proto.make(proto.MSG_STATUS))
        reply = client.recv_msg()
        self.assertEqual(reply["type"], proto.MSG_HEALTH)
        self.assertIn("Fake", reply["libraries"])
        self.assertIsNone(reply["session"])

        # After play, status carries the session id + id.
        client.send(proto.make(proto.MSG_PLAY, id="fake:clip"))
        session_id = client.recv_msg()["session_id"]
        client.send(proto.make(proto.MSG_STATUS))
        reply2 = client.recv_msg()
        self.assertEqual(reply2["session"]["session_id"], session_id)
        self.assertEqual(reply2["session"]["id"], "fake:clip")

    # -- unknown / error handling -------------------------------------------

    def test_play_unknown_id_returns_error(self):
        ps = _build_ps()

        class _Boom(FakeSource):
            def open(self, id: str):
                raise FileNotFoundError(f"no such title {id!r}")

        server = self._start_server(_Boom(ps))
        client = _Client(server)
        self.addCleanup(client.close)

        client.send(proto.make(proto.MSG_PLAY, id="fake:missing"))
        reply = client.recv_msg()
        self.assertEqual(reply["type"], proto.MSG_ERROR)
        self.assertIn("no such title", reply["message"])


if __name__ == "__main__":
    unittest.main()
