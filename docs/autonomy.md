# Autonomy / effort-mode playbook — running unattended

How a future autonomous Claude Code (web) session works on NetVOB_MiSTer **as long
as possible without a human in the loop**. The companion doc
[`session-bootstrap.md`](session-bootstrap.md) covers **prerequisites** (what must be
reachable before useful work starts); **this** doc covers the **run-loop** — how to
stay busy, never block, verify honestly, and never lose work.

> The container is **ephemeral** and runs on a feature branch. The two enemies of an
> unattended session are **blocking on a 30-min build/hardware** and **losing
> uncommitted work**. Everything below is in service of those two.

Cross-refs: [`milestones.md`](milestones.md) (M0–M7), [`dev-workflow.md`](dev-workflow.md)
(build/sim/hardware mechanics + the verification ladder), [`risk-register.md`](risk-register.md)
(why the decoder is the gate), [`service-design.md`](service-design.md) /
[`transport.md`](transport.md) / [`catalog-browse.md`](catalog-browse.md) (the
FPGA-independent Pi/ARM tracks).

---

## 0. Kicking off an unattended run — the `/loop` invocation

An unattended run is started with **one `/loop` command** (the `loop` skill). `/loop`
**with no interval self-paces**: it re-runs the prompt each cycle the instant the prior
cycle's work is durably checkpointed — which is precisely the loop in §7. The canonical
kickoff (fill the `<…>` slots):

    /loop continue the core mission — <MISSION>; but FIRST enumerate every prerequisite
    in docs/session-bootstrap.md and request anything missing from me before starting
    build/HW work, then advance every track that needs nothing withheld; full autonomy
    (commit/push/build/HW-test on <FEAT-BRANCH>, never main); checkpoint to memory each
    cycle (commit+push + docs/progress.md); always use effort ultracode

What each clause binds to in this playbook:

| Clause | Means | See |
|--------|-------|-----|
| `continue the core mission — <MISSION>` | the standing objective each cycle re-derives its next action from; the loop picks the deepest unblocked step toward it | §2, §7 |
| **`request missing prerequisites first`** | **pre-flight gate** — run `verify-session.sh`, diff vs [`session-bootstrap.md`](session-bootstrap.md), post **one** consolidated resource request (SSH to mister/Pi, `.env` creds, DVD dumps, Quartus/build box, Plex/TMDB keys…) **before** any build/HW work; never discover blockers mid-run | §6 + [`session-bootstrap.md`](session-bootstrap.md) |
| `full autonomy (commit/push/build/HW-test …)` | don't stop for routine progress — commit, push, kick detached builds, run sims, inspect filmstrips unprompted | §3, §5 |
| `on <FEAT-BRANCH>, never main` | hard git guardrail: every push lands on the feature branch; `main` is never written | §4 |
| `checkpoint to memory each cycle` | end every cycle durably — **commit+push** the coherent increment AND append a dated one-liner to [`progress.md`](progress.md) | §4 |
| `always use effort ultracode` | spend the high-effort thinking budget — claims-sensitive systems work where a shallow pass over-claims | §1 |

Concrete kickoff for this repo (mission = the whole system, end-to-end on real hardware;
resources requested first):

    /loop continue the core mission — drive NetVOB_MiSTer end-to-end to a fully working
    system on the real SuperStation/MiSTer over SSH (decoder video-out → file via the
    sd_* seam → live PS-over-TCP from the Pi → DVDDumpSource → catalog/browse →
    PlexSource → A/V-sync → field-exact 480i); but FIRST enumerate every prerequisite in
    docs/session-bootstrap.md and request anything missing from me before starting
    build/HW work, then advance every track that needs nothing withheld; full autonomy
    (commit/push/build/HW-test on feat-decoder-bringup, never main); checkpoint to memory
    each cycle (commit+push + docs/progress.md); always use effort ultracode

After the pre-flight request, the loop runs §7 each cycle: read state → pick the deepest
unblocked track (§2) → spawn parallel agents / kick detached builds (§1, §3) → verify up
the ladder (§5) → checkpoint (§4) → repeat; escalate **only** for the genuinely-blocked
few (§7).

> **The "memory" the checkpoint writes to.** Primarily **git** — frequent commits/push
> (§4; an ephemeral container makes uncommitted work = lost). Plus a short human-readable
> trail: one dated line per cycle in [`progress.md`](progress.md) (what advanced / what's
> blocked / what's building). **Single-writer:** only the orchestrator thread touches it,
> never a parallel agent (§5).

## 1. Effort mode & orchestration

- **Run high-effort ("ultracode") mode.** This is a planning-heavy, multi-track
  systems project where a shallow pass produces plausible-but-wrong claims (exactly
  the failure the project's "flag unverifiable claims" ethos guards against). Spend the
  thinking budget.
- **Use multi-agent orchestration.** Most of the project is **not** gated by the FPGA
  decoder (see §2). Independent workstreams should run as **concurrent agents** — send
  **multiple Agent calls in a single message** so they execute in parallel, then
  integrate their results. One agent per coherent workstream (a Pi source plugin, a
  `tools/` harness, the ARM PS-demux logic, an adversarial review pass…).
- **Reserve the main thread as orchestrator.** It owns the run-loop (§7): dispatch
  agents, kick detached builds/sims, integrate, verify, commit. Keep long-lived state
  (what's blocked, what's building) in the main thread, not inside an agent that exits.
- **Coordinate, don't collide.** Two agents must not write the same file, the same
  trace/output dir (§5 single-writer), or the same submodule patch. Partition by
  directory: `service/`, `arm/`, `tools/`, `core/`, `docs/`.

## 2. The parallel-workstream map

The gating fact (Decision D1, [`milestones.md`](milestones.md) M1a, risk #1): **the
Cyclone V decoder has a hardware-verified data path but NO confirmed video output
yet.** Hardware milestones that need *real decoded video* serialize behind it.
**Almost everything else does not** — and even decoder *logic* can advance in **sim**
without a 30-min bitstream or any hardware.

### Independent — can proceed concurrently (no FPGA needed)

| Track | Where | What | Gated by |
|-------|-------|------|----------|
| Pi `Source` interface + catalog | `service/sources/base/`, `service/core/` | The ABC, `CatalogEntry`/`StreamHandle`, two-library aggregator + badging | nothing |
| **DVDDumpSource** (PS passthrough) | `service/sources/dvddump/` | IFO/PGC/VOBU parse → nav-pack-stripped PS; lossless, unit-testable on dump fixtures | nothing |
| **PlexSource** | `service/sources/plex/` | Plex API browse → `ffmpeg` → 480i MPEG-2 PS; isolate private-API behind the plugin (risk #8) | a Plex creds secret (else stub) |
| **PS-over-TCP server** + control channel | `service/core/` | The media socket + `play`/`stop`/`pause`/`seek`/`browse` framing ([`transport.md`](transport.md)) | nothing |
| disc-id utility + metadata | `tools/` | dvdid-style fingerprint → TMDB lookup, sidecar/folder fallbacks; runnable standalone | TMDB key (else fallbacks) |
| **Test harness / tools** | `tools/` | canned 480i+480p PS clips; **vdisk/CHD builder**; **filmstrip tool**; **Verilator conformance→PNG harness**; stub Pi server; `Mount`/`.mra` helper | `ffmpeg` / Verilator local |
| ARM PS-demux + audio-decode logic | `arm/` | PS→ES split (audio-aware from M2), AC-3/MP2→PCM, PTS bookkeeping — **unit-testable off-target** | nothing |
| **Decoder logic in sim** | `core/` + `tools/` | drive `mpeg2fpga` `bench/conformance` under Verilator, resolve `mem_shim`/`waitrequest` hangs, dump a frame **PNG** — **no bitstream, no board** | Verilator local |

### Serialized — gated by confirmed decoder video-out

| Step | Needs | Unblocks |
|------|-------|----------|
| **M1a** confirmed, stable video-out (the gate) | sim PNG first, **then** a bitstream + hardware filmstrip | everything below |
| **M1b** file→decoder via ARM `sd_*` seam | M1a + a `Mount`able test vdisk (built off-target) | M2 |
| **M2** live network ingest | M1b + the Pi stub server (built off-target) | M3 |
| **M3** DVDDumpSource end-to-end | M2 + DVDDumpSource (built off-target) | M4 |
| **M6** A/V sync on **real** video | a real decoded stream with a video presentation clock | M7 polish |

> **Key leverage:** the **Verilator FB-PNG** rung lets decoder *logic* (the project's
> #1 risk) make real progress with **zero** 30-min builds and **zero** hardware. Most
> M1a iteration happens here. Build a bitstream only to confirm what sim already shows.

**Sequencing rule:** at any moment, advance **every** unblocked Independent track in
parallel; only *one* class of work (real-video hardware) is allowed to be blocked, and
when it is, the human is escalated to **only** for that (§7) while the agents keep the
rest moving.

## 3. Never block on long ops

The cardinal rule of an unattended session: **a long operation must never hold the
thread idle.** While it runs, do other workstream work (§2).

- **Quartus builds: launch DETACHED, then poll.** ~30 min, single-thread-bound, must
  survive a laptop sleep/SIGHUP ([`dev-workflow.md`](dev-workflow.md) §4):
  `ssh box "setsid nohup bash runner.sh > /tmp/build.log 2>&1 </dev/null &"`. Then poll
  `/tmp/build.log` (stage / elapsed / warnings / `*.fit.rpt`) — do **not** sit on it.
- **Sims: run in BACKGROUND.** Start the Verilator run with `run_in_background` (it
  re-invokes you on exit) and continue. Long conformance sweeps especially.
- **Wait on a *condition*, not the clock.** Use the **Monitor / until-loop** pattern to
  block on "log says DONE / `.rbf` exists / PNG written," **never a bare `sleep`**
  (foreground `sleep` is blocked anyway). A condition-poll returns the instant the work
  is done and lets you interleave other tasks while waiting.
- **Interleave by default.** Kick the build/sim → dispatch or continue an Independent
  track (§2) → come back when the condition fires. A session that ever sits idle waiting
  on a build is mis-run.

## 4. Checkpoint discipline

The container is **ephemeral** — anything uncommitted dies with it.

- **Commit & push working increments FREQUENTLY** to the feature branch. Never leave
  hours of work uncommitted. A good cadence: every time a track reaches a coherent,
  self-consistent state (a plugin's interface compiles + its unit tests pass; a harness
  produces its artifact; a doc section is internally consistent).
- **Coherent commits.** One commit = one logical unit (mirrors the "one PR per coherent
  unit" rule in [`dev-workflow.md`](dev-workflow.md) §7). Don't dump every track into one
  blob; don't commit a half-edited file.
- `git push -u origin <branch>` early so the remote tracks the branch; push after each
  increment. Per project Git rules: stay on the feature branch, **don't open a PR unless
  asked**.
- **Before a risky/long op, checkpoint first.** Commit clean state before kicking a
  build or a wide refactor, so a container death mid-build costs nothing.
- Vendored deps stay as **pinned submodules + re-appliable patch files**
  ([`dev-workflow.md`](dev-workflow.md) §7) — never commit into a submodule; the patch
  is the durable artifact.

## 5. Verification ladder & honesty

Apply the project's "flag unverifiable claims" ethos **inward**: a green run once is
**not** a result. Climb the ladder ([`dev-workflow.md`](dev-workflow.md) §5, §7) and
gate milestones on the **highest** rung the task can reach.

1. **Unit sim** — module/logic in isolation; assert exact values, not "looks right."
2. **Full-system sim → framebuffer PNG** — drive `mpeg2fpga` `bench/conformance` and
   **dump a decoded-frame PNG**. **Gate milestones on a *correct frame*** (incl.
   interlaced field output), not on "the sim ran."
3. **Hardware** — `Mount` a test vdisk, `load_core`, and **inspect a filmstrip burst
   you actually look at** ([`dev-workflow.md`](dev-workflow.md) §1–2). A single screenshot
   never catches play/seek/boot behavior — burst and view in order.

Discipline that keeps the ladder honest:

- **Single-writer per trace/output dir.** A background writer clobbering a foreground
  run silently corrupts traces → **false conclusions**. Each concurrent agent/run gets
  its **own** output dir. This is the one rule most likely to produce a confident wrong
  answer under multi-agent parallelism (§1) — enforce it hard.
- **REPRODUCE before claiming a milestone.** Boot/init is dominated by uncached SDRAM
  latency; a one-off green run isn't a result — re-run clean and confirm it repeats.
- **Adversarial review pass before merging.** Run independent passes — **logic**;
  **timing / synthesis-safety**; **protocol-vs-reference** (PS framing vs spec, `sd_*`
  vs `mpg_streamer.sv`); **sim-vs-hardware parity**. A spare agent makes a fine
  adversary. This catches the over-claims a single forward pass misses.

## 6. Fail fast

The **SessionStart hook** (`.claude/settings.json` → `scripts/verify-session.sh`,
specified in [`session-bootstrap.md`](session-bootstrap.md)) runs at session start and
**prints what's reachable**: Quartus Docker (`raetro/quartus:17.0`), Verilator,
`ssh mister`, `ffmpeg`, and any creds (Plex/TMDB). Read its report **first**.

It exists so the agent immediately knows **which workstreams it can advance
autonomously vs. which are blocked** — no probing, no guessing:

| Hook says… | Then… |
|------------|-------|
| Verilator OK, hardware **offline** | Advance every Independent track + **decoder sim** (§2). The bulk of the project. Defer only the real-video hardware steps. |
| `ssh mister` OK | Add the hardware rung — M1a bitstream confirm, M1b `Mount`+filmstrip. |
| Quartus Docker OK | Detached `.rbf` builds available (§3); otherwise sim-only, defer bitstreams. |
| No Plex/TMDB creds | PlexSource → **stub**; disc-id → **sidecar/folder fallbacks**. Still progress. |

Knowing the blocked set up front is what makes a long unattended run possible: the
agent spends its time on what it *can* finish instead of stalling on what it can't.

**Request, don't probe** (the §0 `/loop` pre-flight clause). When the mission needs
something the report shows missing, post **one** consolidated resource request to the
human up front — the [`session-bootstrap.md`](session-bootstrap.md) §8 items (SSH to
mister/Pi, `.env` creds, DVD dumps, Quartus/build box, Plex/TMDB keys) — and meanwhile
advance only tracks that need nothing withheld. Never open a build/hardware track you'll
have to abandon for a missing secret.

## 7. Autonomous session loop (pseudo-runbook)

```
on session start:
  1. READ the SessionStart hook report (§6) → compute the reachable/blocked set.
  2. git: confirm feature branch, pull, ensure -u tracking (§4).
  3. PICK the deepest unblocked workstream(s) from the §2 map
       (prefer: anything gating, then the longest Independent tracks).
  4. SPAWN parallel agents for independent pieces — multiple Agent calls in ONE
       message (§1); partition by directory; one output dir each (§5).
  5. KICK long builds/sims DETACHED / in BACKGROUND (§3); record the poll target.
  6. DO OTHER WORK meanwhile — integrate finished agents, advance another track;
       never idle on a build (§3).
  7. when a build/sim condition fires → VERIFY up the ladder (§5):
       sim PNG correct?  filmstrip inspected?  REPRODUCED?  adversarial pass clean?
  8. COMMIT + PUSH the coherent increment (§4).
  9. REPEAT from 3 until no unblocked work remains.

  ESCALATE to the human ONLY for genuinely blocked items:
     • a missing secret (Plex/TMDB creds)            → stub/fallback meanwhile
     • hardware offline (ssh mister / board)          → sim-only meanwhile
     • an architecture decision (e.g. the M1a gate    → flag the contingency menu
       contingency: re-port vs 480p-first vs CD-i        (risk #1) and keep the
       fallback — risk #1)                                Independent tracks moving
  Escalate with the blocker AND what you advanced around it — never just stop.
```

**The shape of a good unattended run:** the gate (decoder video-out) may be blocked on
hardware for long stretches, but the Pi service, the ARM logic, the `tools/` harness,
the disc-id utility, and decoder **sim** fill that time — committed in coherent,
reproduced, adversarially-reviewed increments — so the session is always making real,
durable progress and only surfaces to the human for the few things genuinely outside
its reach.
