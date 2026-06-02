# Catalog / browse design

Goal: on the console, list the **two libraries separately**, make the quality
trade-off obvious (badging), allow selection by list nav **and NFC/Zaparoo**, and
give the dump library a *usable* set of titles despite VOBs carrying no metadata.

---

## 1. Two separate libraries (never merged)

The browse UI shows **two top-level lists**, sourced from the service catalog
aggregator's separate buckets — **no merge, no dedupe**:

```
┌ NetVOB ───────────────────────────────────────┐
│  ▸ DVD Dumps            [field-exact]  (42)     │
│  ▸ Plex                 [transcoded]   (impl.)  │
└────────────────────────────────────────────────┘
```

Selecting a library shows its entries. A title that exists in *both* appears in
*both* — by design. The user always knows which path (and which fidelity) they're
choosing.

## 2. Source badging

Every entry carries a badge from its `Source`:

- **DVD Dumps → `field-exact`** — lossless PS passthrough, true 480i field cadence,
  near-zero CPU.
- **Plex → `transcoded`** — software MPEG-2 re-encode on the Pi (lossy, CPU-bound).

Badges render as a chip on each row and in the now-playing view. Optionally annotate
Plex rows with the source resolution/codec so the user understands what's being
transcoded down to 480i.

## 3. Where the front-end lives

Three viable homes, in increasing integration effort:

1. **HPS-side browse app drawing to the core framebuffer (RECOMMENDED).** A small
   userspace UI on the console that talks to the Pi service's **control channel**
   (`browse`/`play`/…) and renders lists. This mirrors how the SuperStation's
   "Console Mode" already layers a UI on top of MiSTer, and avoids cramming a dynamic,
   network-backed catalog into the static MiSTer OSD.
2. **MiSTer OSD via `CONF_STR`.** The stock core menu is **static text built from the
   core's config string** — fine for settings, poor for a live, network-sourced,
   poster-bearing catalog. Use it for *options* (which library, sync mode), not the
   catalog.
3. **SuperStation "Console Mode" integration.** Best end-user polish (it's the
   native launcher) but couples us to that frontend; treat as a later nicety, behind
   the same control-channel API so it's swappable.

**Decision:** build (1) against the control-channel API; keep (2) for core options;
leave (3) as an optional skin. All three consume the *same* `browse()` data.

## 4. Selection — list nav + NFC/Zaparoo

- **List navigation:** d-pad/controller through the two libraries and their entries;
  select → control-channel `play{id}`.
- **NFC / Zaparoo:** the SuperStation has a built-in **NFC reader** and runs
  **Zaparoo** (formerly TapTo), whose model is *tap a tag → run a mapped action*. We
  register a NetVOB launch action so a tag maps to a specific title:
  - Tag payload (or Zaparoo mapping) → **`{source, id}`** → control-channel
    `play{id}` on the selected source.
  - e.g. a physical "The Matrix" card → `dvddump:matrix-1999` (field-exact), or a
    different card → `plex:<ratingKey>` (transcoded). The *same* movie can have a
    "field-exact" card and a "Plex" card — consistent with the two-library model.
  - **Open item (M4):** confirm Zaparoo's launch-command surface on the SuperStation
    (custom command vs. core-launch) and how a NetVOB title id is best encoded on a
    tag. Park the exact mechanism until M4; the design only needs "tag → {source,
    id}."

## 5. Metadata

### Plex — rich, free
The Plex API gives **titles, posters, art, summaries, season/episode, durations**.
PlexSource maps these straight into `CatalogEntry` (incl. `poster_url`). The Plex
library essentially renders itself.

### DVD dumps — no metadata in the files → disc-ID lookup
VOB/IFO carry **no human title** — just title sets, PGCs, durations. To turn a folder
of dumps into a usable, named, poster-bearing list:

1. **Fingerprint the disc.** Compute a stable **disc ID** from invariant structure:
   - the well-known **"DVD ID"** algorithm (the Windows Media Center / XBMC-era
     `dvdid`: a CRC over `VIDEO_TS.IFO` + `VTS_01_0.IFO` header fields and the title
     set's file sizes/durations), and/or
   - a hash of the IFO program-chain layout + per-title durations (robust to missing
     bytes).
2. **Look it up.** Resolve the fingerprint to a real title via an external DVD/movie
   metadata source:
   - a disc-ID → title database where available (dvdid-style online lookups), then
   - cross-reference to **TMDB** (by resolved title/year) for poster + summary.
3. **Fallbacks (always have a usable name):**
   - **Sidecar file:** honor a user-provided `netvob.json`/`<folder>.nfo` in the dump
     directory (title, year, tmdb id). This always wins and needs no network.
   - **Folder-name heuristic:** parse `The Matrix (1999)/VIDEO_TS` → title/year →
     TMDB match.
   - **Raw fallback:** show the folder name + title/duration so nothing is unbrowsable.
4. **Cache** resolved metadata locally on the Pi (`metadata cache`), keyed by disc ID,
   so lookups are one-time and offline-friendly. Let the user correct a wrong match
   (writes a sidecar).

> The disc-ID step is a *convenience layer in the DVDDumpSource plugin* — it never
> affects the stream path. A dump with zero metadata still plays; it just shows a
> plain name.

## 6. NFC + metadata tie-in

Because tags map to `{source, id}` and the catalog already resolves `id → title/
poster`, a tapped card shows the correct title art on screen before/while it plays —
for both libraries. Writing a tag can be done from the browse UI ("assign this title
to a tag"), capturing the current `{source, id}`.

## 7. Control-channel API the UI uses (recap)

| Action | Message | Returns |
|---|---|---|
| List libraries + entries | `browse` (optionally `{source, path}`) | `{ "DVD Dumps":[entry…], "Plex":[entry…] }` (separate) |
| Play | `play {source, id}` | session/now-playing |
| Transport | `pause` / `resume` / `seek{t}` / `stop` | status |
| Health | `status` | per-source reachable/auth, current session |

Entries include `badge`, `title`, `poster_url?`, `duration_s?` so the UI can render
lists, chips, and (where present) posters uniformly across both libraries.
