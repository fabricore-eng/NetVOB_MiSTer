"""Streamer: pre-buffer + pacing pulls via read(); stops on stop().

Uses a fake StreamHandle (records read sizes) and a fake writer (records
written bytes) plus an injected fake clock so pacing is deterministic.
"""

import service.tests._bootstrap  # noqa: F401

import unittest

from service.core.streamer import State, Streamer
from service.sources.base.source import AvInfo, StreamHandle


class FakeHandle(StreamHandle):
    """In-memory PS source that records every read size and seek target."""

    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0
        self.read_sizes: list[int] = []
        self.seeks: list[float] = []
        self.closed = False
        self.duration_s = 100.0
        self.nav = None
        self.av = AvInfo()

    def read(self, n: int) -> bytes:
        self.read_sizes.append(n)
        chunk = self.data[self.pos : self.pos + n]
        self.pos += len(chunk)
        return chunk

    def seek(self, t_seconds: float) -> None:
        self.seeks.append(t_seconds)
        self.pos = 0

    def close(self) -> None:
        self.closed = True


class FakeWriter:
    def __init__(self):
        self.buf = bytearray()
        self.writes: list[int] = []

    def write(self, data: bytes) -> int:
        self.buf.extend(data)
        self.writes.append(len(data))
        return len(data)


class FakeClock:
    """Deterministic clock; advances only when sleep() is called."""

    def __init__(self):
        self.t = 0.0

    def now(self) -> float:
        return self.t

    def sleep(self, seconds: float) -> None:
        # Record and advance virtual time instead of really sleeping.
        self.t += seconds


class StreamerTest(unittest.TestCase):
    def test_delivers_all_bytes_exactly_and_pulls_via_read(self):
        data = bytes(range(256)) * 10  # 2560 bytes
        h = FakeHandle(data)
        w = FakeWriter()
        s = Streamer(
            h, w, chunk_size=256, prebuffer_bytes=512, rate_bytes_per_s=None
        )
        s.run()

        # All bytes delivered, byte-for-byte, in order.
        self.assertEqual(bytes(w.buf), data)
        # The streamer pulled via read() (not by touching .data directly).
        self.assertTrue(h.read_sizes)
        self.assertTrue(all(n == 256 for n in h.read_sizes))
        self.assertIs(s.state, State.DONE)

    def test_prebuffer_filled_before_first_write(self):
        data = b"X" * 4096
        h = FakeHandle(data)
        w = FakeWriter()
        # prebuffer of 1024 with 256-byte chunks => >=4 reads before 1st write.
        s = Streamer(h, w, chunk_size=256, prebuffer_bytes=1024)
        # Do exactly one step: it must fill the prebuffer (>=4 reads) then emit.
        s.step()
        self.assertGreaterEqual(len(h.read_sizes), 4)
        self.assertGreaterEqual(h.pos, 1024)
        # And it wrote something on that same step.
        self.assertTrue(w.writes)

    def test_stop_halts_and_closes_handle(self):
        data = b"A" * 10_000
        h = FakeHandle(data)
        w = FakeWriter()
        s = Streamer(h, w, chunk_size=256, prebuffer_bytes=512)
        s.step()  # start playing, some bytes out
        partial = len(w.buf)
        self.assertGreater(partial, 0)

        s.stop()
        self.assertIs(s.state, State.STOPPED)
        self.assertTrue(h.closed)

        # After stop, run()/step() must do nothing and write nothing more.
        more = s.run()
        self.assertEqual(more, 0)
        self.assertEqual(len(w.buf), partial)

    def test_pause_resume(self):
        data = b"B" * 4096
        h = FakeHandle(data)
        w = FakeWriter()
        s = Streamer(h, w, chunk_size=256, prebuffer_bytes=256)
        s.step()
        after_one = len(w.buf)
        self.assertGreater(after_one, 0)

        s.pause()
        self.assertIs(s.state, State.PAUSED)
        # While paused, run() returns immediately and emits nothing.
        self.assertEqual(s.run(), 0)
        self.assertEqual(len(w.buf), after_one)

        s.resume()
        self.assertIs(s.state, State.PLAYING)
        s.run()
        # Resumed -> finishes delivering the rest exactly.
        self.assertEqual(bytes(w.buf), data)

    def test_seek_flushes_and_repositions_via_handle(self):
        data = bytes(range(256)) * 8  # 2048 bytes
        h = FakeHandle(data)
        w = FakeWriter()
        s = Streamer(h, w, chunk_size=256, prebuffer_bytes=512)
        s.step()
        s.seek(12.0)
        # Handle was asked to seek.
        self.assertEqual(h.seeks, [12.0])
        # Pre-buffer was flushed; state went back to buffering.
        self.assertIs(s.state, State.BUFFERING)

    def test_pacing_throttles_with_injected_clock(self):
        data = b"P" * 4000
        h = FakeHandle(data)
        w = FakeWriter()
        clk = FakeClock()
        # 1000 B/s; 4000 bytes => ~4s of virtual sleeping accumulated.
        s = Streamer(
            h,
            w,
            chunk_size=1000,
            prebuffer_bytes=0,
            rate_bytes_per_s=1000.0,
            clock=clk.now,
            sleep=clk.sleep,
        )
        s.run()
        self.assertEqual(bytes(w.buf), data)
        # Pacing made virtual time advance (it slept). With no pacing it stays 0.
        self.assertGreater(clk.t, 0.0)

    def test_no_pacing_does_not_sleep(self):
        data = b"Q" * 2000
        h = FakeHandle(data)
        w = FakeWriter()
        clk = FakeClock()
        s = Streamer(
            h,
            w,
            chunk_size=500,
            prebuffer_bytes=0,
            rate_bytes_per_s=None,
            clock=clk.now,
            sleep=clk.sleep,
        )
        s.run()
        self.assertEqual(clk.t, 0.0)


if __name__ == "__main__":
    unittest.main()
