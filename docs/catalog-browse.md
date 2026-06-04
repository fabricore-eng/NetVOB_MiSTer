# Catalog / browse design

Goal: on the console, list the **two libraries separately**, make the quality
trade-off obvious (badging), allow selection by list nav **and NFC/Zaparoo**, and
give the dump library a *usable* set of titles despite VOBs carrying no metadata.

> **Design north-star (user, 2026-06-04): NetVOB *is* an alternate-history DVD player.**
> Imagine a set-top DVD player from a parallel timeline that natively browsed Plex and
> network libraries. The **core just plays video**; everything the user touches — browse,
> selection, transport, on-screen overlays, even disc menus — is framed as that device's
> UI. Concretely that means **two delivery phases that share one aesthetic**: (1) ship
> browse through the **MiSTer OSD** first (zero custom UI), then (2) build the
> **retro-DVD-player Plex-style** front-end + the playback experience in [§8](#8-playback-experience--the-dvd-player). The
> controller is the remote; the screen behaves like a physical player, not a desktop app.

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

**Product decision (user, 2026-06-04): do it all in the MiSTer OSD first.** The core
**just plays video**; browsing the sources/libraries/titles is a **MiSTer-OSD** job and
the **controller drives playback** (see §8). The earlier "build a custom HPS framebuffer
UI first" recommendation is **demoted** to the richer, later phase — we try the
zero-custom-UI OSD path first and only build a bespoke browser if the OSD genuinely
can't carry the experience.

Three homes, now **phased** (MVP → polish):

1. **MiSTer OSD — the MVP browser (DO THIS FIRST, *if it can navigate libraries*).**
   Surface the catalog *through the framework's own UI* so there's no custom front-end
   to build. This phase is **gated on a go/no-go** (validate early in M4): *can the stock
   OSD actually navigate a nested, multi-library catalog?*
   - **OSD file-browser over a console-side catalog view (recommended mechanism).** The
     stock "Load *" picker already browses a directory tree on `/media/fat`. Expose the
     Pi catalog to the console as a **(virtual/synced) folder tree** — *folder per
     source/library* (`DVD Dumps/`, `Plex/`), *entry per title* — so the existing picker
     navigates it with **zero custom RTL/UI**. Selecting an entry triggers the
     mount/`play{source,id}` over the control channel.
   - **`CONF_STR` options** carry settings (which library default, sync mode, aspect),
     not the catalog itself.
   - **Honest constraint (the go/no-go):** the stock OSD/file-picker is a **text list —
     no posters, limited dynamic content.** Titles + folder hierarchy render fine; rich
     metadata/art does not; large libraries need folder nesting/paging. **If** that
     carries "navigate libraries → pick a title → it plays," it's the MVP (badges from §2
     degrade to a text tag, e.g. `The Matrix [field-exact]`). **If** the OSD can't do
     even basic library navigation acceptably, we skip straight to phase 2.
2. **The "alternate-history DVD player" browser — the experience phase (THE VISION).**
   A console-side framebuffer UI (control-channel `browse`/`play`) that renders
   **postered, badged, Plex-style** lists the OSD can't — but styled as **what a DVD
   player from a parallel timeline would show if it had natively browsed Plex and network
   libraries.** Not a generic flat modern grid: the **retro DVD-player idiom** —
   chunky highlight bars, that early-2000s set-top on-screen aesthetic, CRT-native
   (480i-safe fonts, title-safe margins, gentle motion), posters/art where we have them
   (§5). It's the same `browse()` data as the OSD MVP; this is the polish target once
   the spine plays. **This UI and the playback experience (§8) are one product** — see
   the design north-star below.
3. **SuperStation "Console Mode" integration.** Best end-user polish (native launcher)
   but couples us to that frontend; optional skin, behind the same control-channel API.

**Decision:** ship browse on (1) the **OSD** first; upgrade to (2) the framebuffer
browser for posters/art; keep (3) as an optional skin. All consume the *same*
`browse()` data, so the front-end is swappable without touching the sources.

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

## 8. Playback experience — the DVD player

Once a title is selected, **the core just decodes and displays video**; the playback
*experience* (transport, overlays, disc menus) is driven by the **controller as a DVD
remote** + small overlay UI, faithful to the alternate-history-DVD-player north-star.
Sequenced cheapest→richest so each rung ships independently:

### 8a. Transport controls (controller = remote) — *first, both sources*
- Map the pad to player transport: **play/pause**, **stop**, **seek** (scrub ±), **chapter
  prev/next**, **fast-fwd/rew** (where the source supports it). Buttons become
  control-channel messages (`pause`/`resume`/`seek{t}`/`stop`, plus `chapter{±1}`); see
  §7. Latency-sensitive actions (pause) may also be handled console/ARM-side so they feel
  instant, with the Pi told after.
- Chapters/seek map onto **VOBU/GOP boundaries** for the dump source and onto the
  transcode timeline for Plex (lands on M6's GOP-aligned seek).

### 8b. On-screen transport overlay (DVD-player status bar) — *with 8a → richer later*
- The feel: press a button → a **translucent status bar / chapter+time readout** appears
  over the video and auto-hides — like a physical player's OSD.
- **MVP:** reuse the **MiSTer OSD** (it already composites a translucent layer over video)
  to show transport state with minimal work.
- **Authentic version (later):** a small **core-side overlay plane** composited over the
  decoded raster (à la CD-i's plane-mux), so the bar/animation match the retro idiom and
  aren't constrained to the boxy MiSTer OSD. This is a modest RTL add (one text/graphics
  layer + alpha) — schedule alongside the phase-2 browser.

### 8c. DVD features & menus — *the big one; DVD-Dumps source only; LATER*
"All the things a DVD player has" (root/title menus, **subtitle/audio/angle** selection,
chapter menus, resume) is a **substantial, separately-scoped feature** — flag honestly,
don't assume it's free:
- A DVD's menus are **menu VOBs (MPEG-2 video) + subpicture (RLE-coded highlight
  overlays) + navigation commands (the DVD "virtual machine" in the IFO/PGCI)**. Playing
  a disc's real menus = implementing **dvdnav-style navigation**: the **Pi runs the DVD
  VM** (`libdvdnav`) — menu PGCs, button highlight state, nav-command jumps — and streams
  the **menu video through the *same* MPEG-2 decoder**; controller D-pad/Enter map to DVD
  button up/down/left/right/activate over the control channel; the Pi's VM resolves them.
- **New decode/overlay path (the real cost):** **subpicture (SPU) decode + alpha
  compositing** is *not* in `mpeg2fpga`. Decode the RLE SPU (ARM-side) → small RGBA
  highlight → composite into the **core overlay plane** from 8b. (Burning subpicture into
  the video on the Pi is the easy hack but **breaks field-exact passthrough** — so do it
  as an overlay, not a burn-in.)
- **Scope boundary:** real DVD menus apply to **DVD Dumps only.** Plex/web sources have no
  disc menus → they get the §8a/§8b **synthetic** transport UI (and the phase-2 browser
  *is* their "menu"). Subtitle/audio-track *selection* still applies to both where the
  stream carries them.
- **Phasing:** 8a (transport) and 8b-MVP land in/around **M6**; the core overlay plane,
  subpicture, and full disc-menu VM are a **post-M7 experience milestone** — large enough
  to rescope on its own, gated behind the spine working end-to-end.
