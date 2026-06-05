# Spine review — FPGA-independent (service/ + arm/) — 2026-06-05

Adversarial multi-agent review (24 agents, findings adversarially verified). **16 confirmed real
bugs** of 31 candidates. Headline: *the spine is architecturally sound and the happy path works
end-to-end, but is not yet unattended-service-grade or board-ready — bugs cluster in two
ES-corrupting demux defects, a missing live-path backpressure/sd_* seam, and Python-server
lifecycle leaks/races.*

Status legend: ☐ open · ☑ fixed (commit)

## HIGH — ES-corrupting (fix first; they desync the decoder)
- ☑ **arm/ps_demux.c:377-387** — unbounded-PES matched-prefix path drops payload `0x00` bytes
  before a start code (`'77 00 00 00 01 E0'` loses the payload `00`). Fires for unbounded video
  PES (DVD VOB), lands in one 64KiB recv chunk so the 1-byte-feed test misses it. One dropped byte
  permanently desyncs the HW decoder. **FIXED** (emit `(pend_zeros-2)` zeros before the reset,
  mirroring the chunk-exhausted path) + Pass 6 regression test (red/green verified, one-chunk feed).
- ☑ **service/core/ps_demux.py:118-123** — MPEG-1-form PES branch skips only `0xFF` stuffing, never
  the STD_buffer(2)/PTS(5)/PTS+DTS(10) header fields → header bytes leak into the ES before the
  sequence header. In scope: the repo's own pipeline emits MPEG-1 VCD PS. **FIXED** (real MPEG-1 PES
  header skip: stuffing → STD_buffer → PTS/PTS+DTS/0x0F, bounded by `end`) + 5 regression tests
  (PTS / PTS+DTS / STD+PTS / stuffing+PTS / 0x0F), red/green verified.

## HIGH — live-path + lifecycle
- ☐ **arm/netd.c:113-124 (+netingest.c)** — no transport §4 backpressure: recv() never gates on
  `ni_room()` (dead water marks); `es_to_ring_sink` silently drops overflow ES on the on-target
  sd_* path. **Fix:** gate recv on ring room (reuse realpipe.c §4); make `es_dropped>0` a hard error.
- ◐ **server.py seek race (218-229 vs 161-187 + dvddump.py:318-339)** — mid-play seek races
  `handle.read()` (run outside `_lock`) → garbled PS. **PARTIALLY FIXED.** The *handle-level* data
  race (the cited `dvddump.py:318-339`) is fixed: `DVDStreamHandle` + `DVDCellStreamHandle` now guard
  `read()/seek()/close()` with a per-handle lock (their reads are non-blocking, so this can't
  deadlock) — red/green via `test_read_and_seek_are_mutually_exclusive`. The "park the pump" framing
  is NOT viable: the design contract is that `handle.seek()` may run concurrently with a *blocked*
  `read()` and redirect the in-flight read to post-seek data (the gated/transcode case; see
  `test_seek_repositions_stream`), so serializing seek behind the pump's read deadlocks. The remaining
  gap: for a *non-blocking* handle mid-play, `_Session.seek()`→`streamer.seek()`'s `buffer.clear()`
  can still race the pump's `buffer.extend()` (a pre-seek chunk extended after the clear → leak). A
  correct fix needs a handle **position-epoch captured atomically with read-extraction** so the
  streamer can drop only genuinely-stale chunks (epoch alone is ambiguous: an in-flight read across a
  seek returns *valid* post-seek data). Tracked for the M2 live-path hardening pass.
- ☐ **dvddump.py:449/517 + ifo.py:392-417** — two titles in one VTS get identical catalog ids,
  both play episode 1 (VTS_PTT_SRPT + all PGCI_SRP never parsed). **Fix:** encode `vts_ttn`, parse
  PTT_SRPT, select the right PGC.
- ☑ **server.py:161-187 + streamer.py** — source handle never closed on natural EOF/error (leaks an
  ffmpeg subprocess/fd per finished playback for M3/Plex). **FIXED** (the pump's `finally` now calls
  an idempotent `_Session._cleanup()` that closes the handle) + `test_natural_eof_closes_handle...`
  (red/green verified).
- ☑ **server.py:484-517 + 571-576** — sessions never evicted on natural EOF → `_sessions` grows
  unbounded. **FIXED** (folded into `_cleanup()`: an `on_finish=Server._evict` callback pops the
  session from the registry; idempotent w.r.t. `_end_session`) + same regression test.

## MEDIUM
- ◐ **arm/ps_demux.c EOF tail-loss** — no `ps_demux_finalize()`; up to 2 withheld `pend_zeros` lost
  at EOF → last frame truncated. **CORE FIXED** (`ps_demux_finalize()` flushes the withheld trailing
  0x00 run of an unbounded video PES; idempotent; declared in the header with a do-NOT-call-mid-stream
  note) + Pass 8 regression test (red/green verified). **Remaining:** wire a `ni_finalize()` wrapper
  into netd's clean-EOF path so the live ingest actually calls it (tracked with the M2 live-path work).
- ☑ **arm/ps_demux.c:344-351** — runaway header skip on malformed PES (hdr_len > pkt len) swallows
  the next unit. **FIXED** (stage-0 guard: if `hdr_len > pkt_remaining` on a bounded PES, resync to
  start-code instead of overrunning) + Pass 7 regression test (red/green verified).
- ☑ **server.py:580-614 + 130-159** — no double-claim guard on session id → two pumps share one
  handle. **FIXED** (`_Session.bind_media()` sets a `_bound` flag under `_lock`; a second claimant
  raises `OSError("session already bound")` and its socket is closed) + `test_double_claim_is_rejected`
  (red/green verified).
- ☑ **server.py:408-421** — accepted control conn has no `settimeout` → recv hangs forever, defeats
  shutdown. **FIXED** (`conn.settimeout(0.5)` on the accepted control conn; the existing
  `socket.timeout`→continue loop now re-checks `_running`) + `test_shutdown_reaps_idle_control...`
  (red/green verified).
- ☐ **dvddump.py:288-301** — `_fill()` reads an ENTIRE cell span (~296 MB+) into memory, defeating
  the pull/low-mem design. **Fix:** bounded sector-multiple reads, one fh across same-file spans.
- ☐ **dvddump.py:218-241 + 318-339** — unspecified-fps cell → 0 duration → non-monotonic seek map →
  seek mis-lands. **Fix:** non-zero estimate when `playback_time_s is None`.
- ☑ **ifo.py:468-530** — inverted cell (first>last) silently yields 0 spans + negative nr_sectors.
  **FIXED** (reject at parse with IFOParseError + clamp nr_sectors>=0) + 2 tests (red/green verified).

## LOW
- ☑ **server.py:384-404** — `self._threads` grows unbounded (handler threads never pruned).
  **FIXED** (`Server._register_thread()` prunes dead threads under a new `_threads_lock` on every
  spawn; `shutdown()` snapshots under the same lock — also closes a prior unlocked-list data race).

## Completeness (M2/M3)
- **M2 (live ingest):** control/data plane works at loopback; MISSING real backpressure, the actual
  sd_* glue (still `drain_to_file`/stub `ni_get_sector`), the two demux fixes, EOF/reconnect/seek
  resync on the live path, non-loopback bind/`__main__`.
- **M3 (DVDDumpSource):** complete for single-feature-per-VTS; MISSING title-within-VTS (PTT_SRPT),
  fine-grained seek, memory-bounded streaming, corrupt-dump robustness.
