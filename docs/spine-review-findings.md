# Spine review — FPGA-independent (service/ + arm/) — 2026-06-05

Adversarial multi-agent review (24 agents, findings adversarially verified). **16 confirmed real
bugs** of 31 candidates. Headline: *the spine is architecturally sound and the happy path works
end-to-end, but is not yet unattended-service-grade or board-ready — bugs cluster in two
ES-corrupting demux defects, a missing live-path backpressure/sd_* seam, and Python-server
lifecycle leaks/races.*

Status legend: ☐ open · ☑ fixed (commit)

## HIGH — ES-corrupting (fix first; they desync the decoder)
- ☐ **arm/ps_demux.c:377-387** — unbounded-PES matched-prefix path drops payload `0x00` bytes
  before a start code (`'77 00 00 00 01 E0'` loses the payload `00`). Fires for unbounded video
  PES (DVD VOB), lands in one 64KiB recv chunk so the 1-byte-feed test misses it. One dropped byte
  permanently desyncs the HW decoder. **Fix:** emit `(pend_zeros-2)` zeros before the reset,
  mirroring the chunk-exhausted path at lines 409-418. + regression test feeding it as ONE chunk.
- ☐ **service/core/ps_demux.py:118-123** — MPEG-1-form PES branch skips only `0xFF` stuffing, never
  the STD_buffer(2)/PTS(5)/PTS+DTS(10) header fields → header bytes leak into the ES before the
  sequence header. In scope: the repo's own pipeline emits MPEG-1 VCD PS. **Fix:** real MPEG-1 PES
  header skip + tests (STD-only / PTS-only / PTS+DTS).

## HIGH — live-path + lifecycle
- ☐ **arm/netd.c:113-124 (+netingest.c)** — no transport §4 backpressure: recv() never gates on
  `ni_room()` (dead water marks); `es_to_ring_sink` silently drops overflow ES on the on-target
  sd_* path. **Fix:** gate recv on ring room (reuse realpipe.c §4); make `es_dropped>0` a hard error.
- ☐ **server.py seek race (218-229 vs 161-187 + dvddump.py:318-339)** — mid-play seek races
  `handle.read()` (run outside `_lock`) → garbled PS. **Fix:** park the pump before seeking.
- ☐ **dvddump.py:449/517 + ifo.py:392-417** — two titles in one VTS get identical catalog ids,
  both play episode 1 (VTS_PTT_SRPT + all PGCI_SRP never parsed). **Fix:** encode `vts_ttn`, parse
  PTT_SRPT, select the right PGC.
- ☐ **server.py:161-187 + streamer.py** — source handle never closed on natural EOF/error (leaks an
  ffmpeg subprocess/fd per finished playback for M3/Plex). **Fix:** close handle in pump finally.
- ☐ **server.py:484-517 + 571-576** — sessions never evicted on natural EOF → `_sessions` grows
  unbounded. **Fix:** pump finished-callback evicts + closes (fold with handle-close).

## MEDIUM
- ☐ **arm/ps_demux.c EOF tail-loss** — no `ps_demux_finalize()`; up to 2 withheld `pend_zeros` lost
  at EOF → last frame truncated. **Fix:** add finalize, call on clean close.
- ☐ **arm/ps_demux.c:344-351** — runaway header skip on malformed PES (hdr_len > pkt len) swallows
  the next unit. **Fix:** clamp opt-skip to `pkt_remaining`, resync on overflow.
- ☐ **server.py:580-614 + 130-159** — no double-claim guard on session id → two pumps share one
  handle. **Fix:** atomic pop-or-mark; already-bound check.
- ☐ **server.py:408-421** — accepted control conn has no `settimeout` → recv hangs forever, defeats
  shutdown. **Fix:** `settimeout(0.5)` on accepted conn.
- ☐ **dvddump.py:288-301** — `_fill()` reads an ENTIRE cell span (~296 MB+) into memory, defeating
  the pull/low-mem design. **Fix:** bounded sector-multiple reads, one fh across same-file spans.
- ☐ **dvddump.py:218-241 + 318-339** — unspecified-fps cell → 0 duration → non-monotonic seek map →
  seek mis-lands. **Fix:** non-zero estimate when `playback_time_s is None`.
- ☐ **ifo.py:468-530** — inverted cell (first>last) silently yields 0 spans + negative nr_sectors.
  **Fix:** validate `last>=first`, clamp.

## LOW
- ☐ **server.py:384-404** — `self._threads` grows unbounded (handler threads never pruned).

## Completeness (M2/M3)
- **M2 (live ingest):** control/data plane works at loopback; MISSING real backpressure, the actual
  sd_* glue (still `drain_to_file`/stub `ni_get_sector`), the two demux fixes, EOF/reconnect/seek
  resync on the live path, non-loopback bind/`__main__`.
- **M3 (DVDDumpSource):** complete for single-feature-per-VTS; MISSING title-within-VTS (PTT_SRPT),
  fine-grained seek, memory-bounded streaming, corrupt-dump robustness.
