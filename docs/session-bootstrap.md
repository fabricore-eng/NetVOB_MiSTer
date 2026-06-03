# Session bootstrap — prerequisites & assets for an unattended cloud run

What must be **provisioned up front** before a future autonomous Claude Code (web)
session can do useful work on NetVOB_MiSTer, separated from what the agent can fetch
itself. The session runs in an **ephemeral cloud container** that clones the repo
fresh and is governed by a **network policy** — so anything it cannot reach or
download must be staged by a human first.

> Companion: [`autonomy.md`](autonomy.md) covers the **run-loop** (how to stay busy,
> never block, verify honestly, never lose work). **This** doc is strictly
> **PREREQUISITES / ASSETS** — what to provision, not how to run. Read the
> SessionStart hook report (§7) first, then this checklist (§8) is the human's job.

Legend used throughout:

- ✅ **STAGE** — a human must provision this; the agent **cannot** obtain it on its own.
- 🔄 **AGENT-FETCHES** — the agent pulls this itself; **don't** stage it (but **pin** it, §4).

Cross-refs: [`milestones.md`](milestones.md) (M0–M7), [`dev-workflow.md`](dev-workflow.md)
(build/sim/hardware mechanics), [`risk-register.md`](risk-register.md),
[`service-design.md`](service-design.md), [`transport.md`](transport.md),
[`catalog-browse.md`](catalog-browse.md).

---

## 1. TL;DR + the single biggest gotcha

**The cloud container is x86 but almost certainly cannot, by itself, (a) run a ~30‑min
Quartus build or (b) reach your LAN hardware (SuperStation One, Raspberry Pi 5, Plex).**
Two things must be true for hardware/bitstream work to happen at all:

1. **The environment's network policy must permit egress** to your LAN and to the
   external services (GitHub, Docker registry, the source repos, Plex, TMDB). Configure
   this per Claude Code on the web's network-policy docs:
   <https://code.claude.com/docs/en/claude-code-on-the-web> . A restricted/default
   policy means **no LAN reach → sim-only**.
2. **A reachable, always-on x86 build/test box must be provisioned** that the session
   **SSHes into** to run Quartus (`raetro/quartus:17.0` Docker,
   [`dev-workflow.md`](dev-workflow.md) §4) and to reach the hardware loop (`ssh mister`
   + the Pi). Do **not** assume the container can `docker run` Quartus locally or open a
   socket to the console — treat the build box as the executor and the container as the
   orchestrator.

| If you provision… | The agent can do… |
|---|---|
| Nothing extra (default policy) | **Nothing useful** — likely can't even clone deps. Fix the policy first. |
| Network policy + repos/Docker reachable, **no** build box, **no** LAN | All sim/logic/Pi-service/ARM/`tools/` work (the bulk, see [`autonomy.md`](autonomy.md) §2) — but **no `.rbf` builds, no hardware**. |
| + always-on x86 build box (SSH) reachable | + detached Quartus builds (`.rbf`). |
| + LAN egress to SuperStation & Pi | + the full hardware loop: `load_core`/`Mount`/`screenshot`/filmstrip, live network ingest. |
| + Plex/TMDB reachable + creds | + PlexSource real (not stub) + disc-ID metadata lookups. |

**Good news:** per [`autonomy.md`](autonomy.md) §2, the **majority** of the project
(Pi `Source` plugins, PS‑over‑TCP server, ARM PS‑demux logic, `tools/` harness, **and
decoder bring-up in Verilator sim → framebuffer PNG**) needs **no Quartus and no
hardware**. So even a sim‑only container is highly productive; staging the build box +
LAN just unblocks the serialized hardware rungs (M1a bitstream confirm onward).

---

## 2. ✅ Secrets & credentials (stage via a gitignored `.env`)

Stage these as a **gitignored** `.env` at the repo root (copy `.env.example` → `.env`,
§7). **This doc documents the KEYS only — never values.** The repo's `.gitignore`
already excludes `.env` (and build/sim artifacts + large test media), so secrets never
get committed.

| Key | ✅/🔄 | Purpose | Notes |
|---|---|---|---|
| `MISTER_HOST` / `MISTER_USER` / `MISTER_SSH_KEY` | ✅ STAGE | The `ssh mister` hardware loop: `load_core`/`Mount`/`screenshot` via `/dev/MiSTer_cmd` ([`dev-workflow.md`](dev-workflow.md) §1) | Host/IP of the **SuperStation One** on the LAN, user (`root`), path to a private key. Equivalent to a `~/.ssh/config` `Host mister` block. |
| `PI_HOST` / `PI_USER` / `PI_SSH_KEY` | ✅ STAGE | SSH to the **Raspberry Pi 5** where the provider service runs (deploy/run `service/`, stage dumps, stub server) | |
| `BUILDBOX_HOST` / `BUILDBOX_USER` / `BUILDBOX_SSH_KEY` | ✅ STAGE | SSH to the **always-on x86 build box** that runs Quartus Docker (the container can't, §1) and the detached `.rbf` builds | Must have Docker + the `raetro/quartus:17.0` image (or net access to pull it) and ideally LAN line-of-sight to the hardware. |
| `PLEX_URL` / `PLEX_TOKEN` | ✅ STAGE | **PlexSource** — Plex API browse + file access ([`service-design.md`](service-design.md) §3.2) | `PLEX_TOKEN` is the `X-Plex-Token`. Absent → PlexSource runs as a **stub** ([`autonomy.md`](autonomy.md) §6). |
| `TMDB_API_KEY` | ✅ STAGE | Disc-ID → title **metadata lookups** for DVD dumps ([`catalog-browse.md`](catalog-browse.md) §5) | Or the chosen provider's key. Absent → disc-ID falls back to sidecar/folder-name ([`catalog-browse.md`](catalog-browse.md) §5.3). |
| GitHub push creds | 🔄 (provided in-session) | Commit/push to the feature branch | **Already provided** by the session; do **not** stage. Stay on the feature branch, no PR unless asked ([`autonomy.md`](autonomy.md) §4). |

**Sample `.env` key list** (values redacted — `.env.example` ships these keys empty):

```dotenv
# --- SuperStation One (the `ssh mister` hardware loop) ---
MISTER_HOST=
MISTER_USER=root
MISTER_SSH_KEY=        # path to private key, e.g. ~/.ssh/mister_ed25519

# --- Raspberry Pi 5 (provider service host) ---
PI_HOST=
PI_USER=
PI_SSH_KEY=

# --- x86 build box (runs Quartus Docker; the container can't) ---
BUILDBOX_HOST=
BUILDBOX_USER=
BUILDBOX_SSH_KEY=

# --- PlexSource (else PlexSource = stub) ---
PLEX_URL=              # e.g. http://192.168.1.10:32400
PLEX_TOKEN=            # X-Plex-Token

# --- Disc-ID metadata (else sidecar/folder fallbacks) ---
TMDB_API_KEY=

# --- Staged DVD dumps (VOB/VIDEO_TS) path the service reads ---
DVD_DUMPS_DIR=

# GitHub push credentials are provided in-session — do NOT put them here.
```

> SSH keys: either reference key **paths** the container can read (mounted/staged) or
> store the key material in a secrets mechanism the policy supports — never commit keys.
> Prefer a dedicated, least-privilege key per host.

---

## 3. ✅ User-owned / physical assets the agent can't download

Physical or personally-owned media. The agent has **no way to acquire these** — stage
them and make sure everything is **powered on and on the network during the session.**

| Asset | ✅ STAGE | Why / which milestone | Recommendation |
|---|---|---|---|
| **DVD dumps** (VOB/`VIDEO_TS`) | ✅ STAGE | DVDDumpSource + decoder validation. Staged at a **known path** on the Pi (and/or build box) — record it in `.env`/a path var the service reads | Stage **at least three**: see the matrix below. These are your own decrypted dumps ([`service-design.md`](service-design.md) §3.1 assumes already-decrypted). |
| **Physical NFC tags** | ✅ STAGE | **M4** Zaparoo "tap → `{source,id}` → play" ([`catalog-browse.md`](catalog-browse.md) §4) | A couple of writable tags; the agent can't provision the SuperStation's NFC reader or a card. |
| **CRT** (or HDMI-capture/scope) | ✅ STAGE | **M7** field-exact / 480i / 3:2-pulldown verification ([`milestones.md`](milestones.md) M7, risk #2) | A true field-exact check needs eyes on a CRT or a capture device. Absent → the agent can only go as far as **screenshot / framebuffer / filmstrip** evidence ([`dev-workflow.md`](dev-workflow.md) §1) and must flag field-cadence as **unverified**. |
| **All hardware powered + networked** | ✅ STAGE | The whole hardware loop | SuperStation, Pi 5, build box, Plex server, NFC reader — all up and reachable for the duration. A sleeping box = a blocked rung. |

**Suggested DVD-dump fixture set** (covers the cases the milestones exercise):

| Fixture | Purpose | Milestone |
|---|---|---|
| One **short clip** (a minute or two) | Fast iteration — quick sim/`Mount`/filmstrip turnarounds | M0–M1 |
| One **interlaced title with 3:2 film pulldown** | The field-exact / film-cadence validation target | **M7** (risk #2), tracked from M1 |
| One **multi-angle / multi-title** disc | DVDDumpSource nav edge cases (PGC/cell ordering, angles) | M3 |

> If you can't stage all three, the short clip is the minimum to start; the
> film-pulldown title is what makes M7's headline claim checkable, and the multi-title
> disc is what stresses VOBU nav correctness (risk #5).

---

## 4. 🔄 What the agent fetches itself — DON'T stage, but PIN for determinism

These are public; the agent pulls them (given network egress). **Do not stage them** —
but **pin** them so an ephemeral container reproduces the same inputs every run.
Vendored repos go in `core/` as **git submodules pinned to a SHA**, with our edits as
**re-appliable patch files** ([`dev-workflow.md`](dev-workflow.md) §7).

### Repos to vendor (pin as submodules)

| Repo | Role | Default branch | Suggested pin (fetched 2026-06-03 — ⚠️ **re-verify**, see note) |
|---|---|---|---|
| `OldRepoPreservation/mpeg2fpga` (+ OpenCores) | The MPEG-2 decoder RTL (BSD; Verilog) | `master` | `2d51ffc21d5a8372cbfaa6be9dfde4a5b863245a` |
| `mrchrisster/MiSTer_MPEG2` | The Cyclone V port we fork (`mpg_streamer.sv`, `mem_shim.sv`) | `main` | `11d1aa2d11649d0c4048d1b33fb1d2abc84771ef` |
| `MiSTer-devel/CDi_MiSTer` (upstream `Slamy/CDi_MiSTer`) | Best flow template; MPEG-1 FMV reference | `main` | `ae583cd049a31cd44a10f6b744898275363a4ac2` |
| `MiSTer-devel/Template_MiSTer` | Stock `sys/` framework (target `5CSEBA6U23I7`) | `master` | `f35083f3b40d24853abea4cd3f77caccbd71d5de` |
| `MiSTer-devel/Main_MiSTer` | `hps_io.sv` / `user_io.cpp` / `fpga_io.cpp` reference | `master` | `1f5337ee2cba8c62c361b1c044a2966b75c9ac67` |
| `mrchrisster/mister_cdi_vcd_creator` | Mux-side reference (MPEG-1 VCD) | `main` | `343d4e246f657058432aaa65612a95c263c6c3fc` |

> ⚠️ **Pin-verification caveat.** The SHAs above were read off the GitHub commit pages
> via an automated page-parse on **2026-06-03**, **not** confirmed with `git`. Per the
> project's "flag unverifiable claims" ethos: **at M0, re-verify each with
> `git ls-remote <url> HEAD` (or the branch ref) and pin the confirmed value** before
> adding the submodule. Treat these as *starting candidates*, not ground truth. If a
> repo has since advanced, pin the SHA you actually vendor and record it here.

### Tools the agent installs / pulls (pin versions at M0)

| Tool | 🔄 | Use | Pin note |
|---|---|---|---|
| Docker image `raetro/quartus:17.0` | 🔄 | Quartus 17.0.2 Lite build, no Intel login ([`dev-workflow.md`](dev-workflow.md) §4) | Pin the **tag `17.0`** (and ideally the image **digest**) on the **build box**, not the container. |
| **Verilator** | 🔄 | The #1 de-risk: sim decoder → framebuffer PNG ([`dev-workflow.md`](dev-workflow.md) §5) | Record exact version at M0. |
| **iverilog** | 🔄 | Alt/secondary Verilog sim | |
| **ffmpeg** | 🔄 | Build canned PS test clips; PlexSource transcode ([`transport.md`](transport.md)) | Needs `mpeg2video` enc + `-f vob`/`-f dvd` mux. |
| **mjpegtools** (`mpeg2enc` + `mplex`) | 🔄 | MPEG-1 VCD creation reference (`mister_cdi_vcd_creator`) | |
| **vcdimager** | 🔄 | VCD authoring (CD-i M0 baseline) | |
| **chdman** (via `rom-tools`, **not** the giant `mame` formula) | 🔄 | Build/inspect CHD + test vdisk images ([`dev-workflow.md`](dev-workflow.md) §3) | `brew install rom-tools`. |
| **Python** + **plexapi** + **libdvdread/dvdnav** | 🔄 | `service/` orchestration, Plex API, IFO/VOBU parse ([`service-design.md`](service-design.md)) | Pin via a `service/requirements.txt`/lockfile at M0. |

> **Determinism rule:** create the **M0 pin manifest** (a table like the two above,
> committed once the values are `git`-confirmed and the tool versions are recorded) so
> every later container reproduces the same toolchain + RTL. A moving HEAD is a
> non-reproducible build.

---

## 5. ✅/policy — what the network policy must reach

For the agent to do the **full** job, the policy must allow egress to all of:

| Endpoint | ✅ STAGE (policy) | Needed for | If blocked |
|---|---|---|---|
| **SuperStation One** (LAN) | ✅ | `ssh mister` hardware loop | No hardware rungs → sim-only |
| **Raspberry Pi 5** (LAN) | ✅ | Deploy/run the service; live ingest | No live network-ingest tests (M2+) |
| **x86 build box** (LAN/host) | ✅ | Quartus builds (§1) | No `.rbf` builds → sim-only |
| **Plex server** (LAN) | ✅ | PlexSource real path | PlexSource = stub |
| **GitHub** (github.com) | ✅ | Clone deps, push the branch | Can't vendor submodules / push work |
| **Docker registry** | ✅ | Pull `raetro/quartus:17.0` (on the build box) | Pre-pull the image on the box |
| **The source repos** (§4) | ✅ | The RTL/reference code | Pre-clone/mirror them |
| **TMDB** (api.themoviedb.org) | ✅ | Disc-ID metadata | Falls back to sidecar/folder names |

> **If the policy is restricted and can't reach the LAN**, the agent **cannot** touch
> hardware — the human must choose an environment/policy that permits it, and possibly
> a **tunnel/VPN** from the cloud environment into the LAN (so the build box and console
> are reachable). See the network-policy docs:
> <https://code.claude.com/docs/en/claude-code-on-the-web> . Decide this **before** the
> run; the agent can't widen its own policy.

---

## 6. Licensing / account notes

- **Quartus** is avoided as a licensed/account install by using the **`raetro/quartus:17.0`
  Docker image** (Quartus 17.0.2 Lite, **no Intel login**, [`dev-workflow.md`](dev-workflow.md)
  §4). **Confirm Docker is actually available on the build box** — if it isn't, you're
  back to a manual Intel-account Quartus Lite install, which the agent can't complete
  unattended.
- **`mpeg2fpga`** is **BSD**, but confirm the **exact variant (2- vs 3-clause)** before
  any redistribution of vendored RTL — open watch-item in [`risk-register.md`](risk-register.md).
- **Plex** uses parts of a **private API**; respect Plex ToS and keep all Plex specifics
  quarantined in `sources/plex/` ([`service-design.md`](service-design.md) §3.2, risk #8).
- **TMDB** requires accepting its API **terms**; the key is per-account. Honor rate
  limits and attribution.

---

## 7. The SessionStart hook (authored separately — described here)

A SessionStart hook is **provided**: `.claude/hooks/session-start.sh` (a thin wrapper)
runs **`scripts/verify-session.sh`** at session start. **To enable it**, register it in
**`.claude/settings.json`** with a one-line `SessionStart` entry — left **unregistered by
default**, because auto-running a hook is a self-modification to opt into deliberately
(the snippet is in the wrapper's header comment). It performs a **pre-flight
reachability/asset check** and prints a clear **✅/❌ report**, but **does NOT block**
session start. You can also just run `scripts/verify-session.sh` by hand anytime.

What it checks:

| Check | Pass means |
|---|---|
| **Build box / Docker reachable** | `ssh $BUILDBOX_HOST` works and `raetro/quartus:17.0` is present/pullable → `.rbf` builds available |
| **Verilator + ffmpeg present** | The sim + clip-building rungs are available (the productive sim‑only floor) |
| **`ssh mister` works** | The hardware loop (`load_core`/`Mount`/`screenshot`) is reachable |
| **Pi SSH works** (`ssh $PI_HOST`) | The service host is reachable for deploy/live-ingest |
| **`.env` present + required keys set** | Secrets are staged (§2); flags which optional creds are missing (Plex/TMDB → stub/fallback) |
| **Staged DVD dumps present** | The fixture path (§3) exists and is non-empty |
| **Submodules at pinned SHAs** | `core/` submodules are checked out at the §4 pins (reproducible inputs) |

The report is the agent's **map of which workstreams are unblocked vs blocked**
([`autonomy.md`](autonomy.md) §6) — it reads it **first** and spends its time on what it
*can* finish. Because the hook is non-blocking, a partially-provisioned environment
still starts and still gets the sim/service/ARM/`tools/` work done.

> **Human:** copy **`.env.example` → `.env`** and fill in the keys from §2 before the
> run. Missing keys don't stop the session — they just downgrade the corresponding
> track to a stub/fallback and show ❌ in the report.

---

## 8. One-time human PRE-FLIGHT CHECKLIST

Do these **once**, before kicking off an autonomous run. (Consolidates §§1–7.)

**Network & compute**
- [ ] Pick/configure a Claude Code on the web **network policy** that reaches the LAN +
      GitHub + Docker + repos + Plex + TMDB (§5;
      <https://code.claude.com/docs/en/claude-code-on-the-web>). Add a **tunnel/VPN** if
      the cloud env can't see the LAN directly.
- [ ] Provision an **always-on x86 build box**, reachable by SSH from the session, with
      **Docker** + the **`raetro/quartus:17.0`** image pulled (§1, §6).
- [ ] Confirm the build box has LAN line-of-sight to the SuperStation + Pi (for the
      hardware loop), or accept build-only there.

**Secrets (`.env`)**
- [ ] `cp .env.example .env` and fill: `MISTER_*`, `PI_*`, `BUILDBOX_*`, `PLEX_URL`/`PLEX_TOKEN`,
      `TMDB_API_KEY` (§2). Leave GitHub creds out (provided in-session).
- [ ] Confirm **`.env`** is gitignored (the repo's `.gitignore` already excludes it) so
      secrets aren't committed.
- [ ] Stage each SSH **private key** where the container can read it; use least-privilege keys.

**Physical / owned assets**
- [ ] Stage **DVD dumps** at the known path on the Pi/build box — ideally the **3-fixture
      set**: short clip + interlaced 3:2-pulldown title + multi-angle/multi-title disc (§3).
- [ ] Have **NFC tags** on hand for M4 (§3).
- [ ] Connect a **CRT or HDMI-capture/scope** for M7 field-exact verification (else
      accept screenshot/framebuffer/filmstrip evidence only) (§3).
- [ ] Power on and network **all** hardware (SuperStation, Pi 5, build box, Plex, NFC
      reader) for the run's duration (§3).

**Reproducibility (pins)**
- [ ] At M0, **`git ls-remote`-verify** the §4 repo SHAs and pin the **confirmed**
      values as `core/` submodules; record tool versions in the M0 pin manifest (§4).
- [ ] Confirm `mpeg2fpga`'s **BSD variant** before redistributing vendored RTL (§6).

**Sanity**
- [ ] Run / read **`scripts/verify-session.sh`** output (§7) — every ✅ you expect is
      green; understand which ❌ items downgrade to stub/fallback.

---

## Definition of ready — the agent can work autonomously when…

- The **network policy** lets it reach GitHub + Docker + the source repos (so it can
  vendor deps and push) — **and**, for hardware work, the LAN (SuperStation, Pi, build
  box) + Plex + TMDB (§5).
- An **always-on x86 build box** with Quartus Docker is SSH-reachable (the container
  can't build locally, §1) — **or** the run is knowingly **sim-only** and that's accepted.
- **`.env`** is present with the §2 keys (missing optional creds just stub PlexSource /
  fall back disc-ID — still productive).
- The **DVD-dump fixtures** are staged at the known path; for M7, a **CRT/capture** is
  connected (§3).
- The **`core/` submodules are pinned** to `git`-confirmed SHAs and tool versions are
  recorded (§4) — reproducible inputs.
- **`scripts/verify-session.sh`** runs at start and the agent has read its ✅/❌ map (§7),
  so it knows its reachable vs blocked set before doing anything ([`autonomy.md`](autonomy.md)).

When those hold, the agent can run the [`autonomy.md`](autonomy.md) loop: advance every
unblocked track in parallel, escalate to the human **only** for genuinely blocked items
(a missing secret, offline hardware, or the M1a architecture gate), and keep durable,
reproduced, committed progress flowing the rest of the time.
