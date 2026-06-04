# Morning briefing — resume here

One-page summary of the overnight autonomous run so you don't have to scroll the cycle log.
Full detail: [`progress.md`](progress.md). HW localization detail: [`hw-decode-diagnostic.md`](hw-decode-diagnostic.md).

## Where we are
- ✅ **Decoder de-risked in sim** — static + real I/P/B motion + NTSC 480i geometry + field parity + PSNR; DVDDumpSource & ARM pipeline validated on your **real KUNGPOW dump**.
- ✅ **Analog 480i timing FIXED on real hardware** — the CRT went from scramble → **stable locked raster** (the hard analog frontier).
- ⏳ **THE ONE OPEN GATE: confirmed *decoded video* on hardware.** Last night the screen locked but was **black** (correct raster, no decoded pixels). Sim says a *conformant* decode path works, so the suspect is HW-only (mem_shim response handling / feed). A candidate fix is built and waiting.

## Do this first (one command, at the SuperStation)
```sh
tools/build/hw_decode_test.sh
```
It acquires the `mister` lock, loads the **candidate `.rbf` (480i + ADDR_ERR fix)**, mounts a test clip, dumps the fork's `uart_debug` counters, and auto-releases. **Watch the CRT** + paste me the `uart_debug` line.
- **Picture appears** → 🎉 decode-on-HW solved.
- **Still black** → the `uart_debug` counts localize it (decision tree in `hw-decode-diagnostic.md`): `streamer_*`=0 → feed bug; frames=0 → decoder/FIFO; `rd≠rsp` → mem_shim; all-advancing → analog. Backup patches staged in `core/patches/hw/` (FIFO-swap, feed-latch).

## Built overnight (all committed/pushed on `feat-decoder-bringup`, each a rollback point)
- Candidate `.rbf` (480i + ADDR_ERR) — on the Dell, morning-ready.
- ADDR_ERR collision: **proven** data-corruption hazard + sim-validated fix (lead candidate).
- Gray dual-clock FIFO: **functionally exonerated** in sim (bit-identical) — kept as a physical/timing backup only.
- **M3**: DVDDumpSource PGC/cell nav → ordered field-exact streaming (verified on KUNGPOW).
- **M4**: `tools/discid/` DVD fingerprint + title-metadata resolver (sidecar/cache/folder/TMDB).
- Full regression green (service 79, arm all, discid 19, decoder-sim lint clean).

## Waiting on you (non-blocking)
- **TMDB API key** + **Plex URL/token** — you said you'd provide; `.env` still has them empty (PlexSource = stub, disc-id = folder fallback until then).
- **The hardware decode test itself** (needs your eyes on the CRT).
- Backlog idea logged: a 3rd **web/live-stream source** (Toonami Aftermath → Pi ffmpeg → 480i PS), design-only.

`main` and `mister` were untouched all night (off-board constraint). Ready when you are.
