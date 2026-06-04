"""Streamer — drives a media socket from ``StreamHandle.read()``.

Per ``service-design.md`` §2 and ``transport.md`` §4: a small **pre-buffer**
absorbs source jitter (Plex transcode / seek restarts; tiny for DVD), then the
streamer **paces** bytes out the media socket. The decoder's pull is the
ultimate pacer — TCP flow control backpressures the Pi if the ARM stops
reading — so this module's pacing is just a cheap rate cap to keep the
pre-buffer from being dumped in one burst.

This is **pure logic**: the socket is an injected *writer* (anything with a
``write(bytes) -> int`` / callable). That keeps it unit-testable with a fake
sink and reusable on the console ARM for local sources. Control actions
(``pause``/``resume``/``seek``/``stop``) manipulate the handle.

A ``clock`` and ``sleep`` are injected (default: ``time.monotonic`` /
``time.sleep``) so tests run instantly and deterministically.
"""

from __future__ import annotations

import time
from enum import Enum
from typing import Callable, Optional, Protocol

from service.sources.base.source import StreamHandle


class Writer(Protocol):
    """The injected media sink. A real socket's ``.sendall`` wrapped to return
    the count, or any object exposing ``write(bytes) -> int``."""

    def write(self, data: bytes) -> int: ...


class State(Enum):
    IDLE = "idle"
    BUFFERING = "buffering"
    PLAYING = "playing"
    PAUSED = "paused"
    STOPPED = "stopped"
    DONE = "done"  # reached EOF on the handle


class Streamer:
    """Drives one media socket from one ``StreamHandle``.

    Parameters
    ----------
    handle:
        The source's pull iterator of PS bytes.
    writer:
        Injected sink with ``write(bytes) -> int``.
    chunk_size:
        Bytes pulled per ``read()`` and written per step.
    prebuffer_bytes:
        How many bytes to accumulate before the first write (the pre-buffer).
    rate_bytes_per_s:
        Soft pacing cap. ``None`` / ``0`` disables pacing (pull as fast as the
        writer accepts — TCP still backpressures in production).
    clock / sleep:
        Injected time source and sleeper for deterministic tests.
    """

    def __init__(
        self,
        handle: StreamHandle,
        writer: Writer,
        *,
        chunk_size: int = 32 * 1024,
        prebuffer_bytes: int = 64 * 1024,
        rate_bytes_per_s: Optional[float] = None,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if chunk_size <= 0:
            raise ValueError("chunk_size must be positive")
        if prebuffer_bytes < 0:
            raise ValueError("prebuffer_bytes must be >= 0")
        self.handle = handle
        self.writer = writer
        self.chunk_size = chunk_size
        self.prebuffer_bytes = prebuffer_bytes
        self.rate_bytes_per_s = rate_bytes_per_s
        self._clock = clock
        self._sleep = sleep

        self.state = State.IDLE
        self.bytes_written = 0
        self._buffer = bytearray()
        self._eof = False
        # Pacing accounting.
        self._start_time: Optional[float] = None

    # -- control surface -----------------------------------------------------

    def pause(self) -> None:
        """Stop emitting bytes; keep the handle and pre-buffer intact."""
        if self.state in (State.PLAYING, State.BUFFERING):
            self.state = State.PAUSED

    def resume(self) -> None:
        """Resume emitting after a pause."""
        if self.state is State.PAUSED:
            self.state = State.PLAYING

    def seek(self, t_seconds: float) -> None:
        """Seek the underlying handle and flush the in-flight pre-buffer.

        Per ``transport.md`` §6, on seek we flush stale buffered bytes so no
        pre-seek data reaches the decoder; the handle re-positions to the
        nearest preceding GOP/VOBU.
        """
        self.handle.seek(t_seconds)
        self._buffer.clear()
        self._eof = False
        # Re-enter buffering so the new position re-fills the pre-buffer.
        if self.state in (State.PLAYING, State.PAUSED, State.DONE):
            self.state = State.BUFFERING
        # Reset pacing baseline so the post-seek burst isn't throttled
        # against the pre-seek clock.
        self._start_time = None

    def stop(self) -> None:
        """Stop the session and close the handle. Terminal."""
        if self.state is not State.STOPPED:
            self.state = State.STOPPED
            self._buffer.clear()
            self.handle.close()

    # -- internals -----------------------------------------------------------

    def _fill_prebuffer(self) -> None:
        """Pull from the handle until the pre-buffer is full or EOF."""
        while not self._eof and len(self._buffer) < self.prebuffer_bytes:
            chunk = self.handle.read(self.chunk_size)
            if not chunk:
                self._eof = True
                break
            self._buffer.extend(chunk)

    def _pace(self) -> None:
        """Sleep just enough to honor ``rate_bytes_per_s`` (if set)."""
        rate = self.rate_bytes_per_s
        if not rate:
            return
        now = self._clock()
        if self._start_time is None:
            self._start_time = now
            return
        elapsed = now - self._start_time
        target_bytes = elapsed * rate
        if self.bytes_written > target_bytes:
            ahead = self.bytes_written - target_bytes
            self._sleep(ahead / rate)

    def _step(self) -> bool:
        """Do one unit of work. Returns ``True`` while more work may remain.

        Returns ``False`` once the stream is finished/stopped or while paused
        (so a caller's ``while streamer.step(): pass`` loop yields on pause).
        """
        if self.state in (State.STOPPED, State.DONE):
            return False
        if self.state is State.PAUSED:
            return False

        if self.state in (State.IDLE, State.BUFFERING):
            self.state = State.BUFFERING
            self._fill_prebuffer()
            self.state = State.PLAYING

        # PLAYING: top up (so the buffer keeps a chunk of lookahead) and emit.
        if not self._eof and len(self._buffer) < self.chunk_size:
            chunk = self.handle.read(self.chunk_size)
            if not chunk:
                self._eof = True
            else:
                self._buffer.extend(chunk)

        if self._buffer:
            out = bytes(self._buffer[: self.chunk_size])
            del self._buffer[: self.chunk_size]
            self._pace()
            self.writer.write(out)
            self.bytes_written += len(out)
            return True

        # Buffer drained.
        if self._eof:
            self.state = State.DONE
            return False
        return True

    def step(self) -> bool:
        """Public single-step. See ``_step``."""
        return self._step()

    def run(self, max_steps: Optional[int] = None) -> int:
        """Pump until the stream finishes/stops (or ``max_steps`` reached).

        Returns the number of steps that performed a write/pull. Stops cleanly
        on ``DONE``/``STOPPED``; if called while ``PAUSED`` it returns
        immediately (the caller drives pause/resume via the control channel).
        """
        steps = 0
        while True:
            if max_steps is not None and steps >= max_steps:
                break
            if not self._step():
                break
            steps += 1
        return steps
