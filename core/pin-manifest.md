# core/pin-manifest.md — reproducibility pins

Frozen inputs for an ephemeral / unattended run. Every upstream repo we depend on is
pinned to an exact commit so a fresh container reproduces the same RTL and reference
code every time. Vendored repos are git submodules under `core/` checked out at the
pin; not-yet-vendored repos are pinned here and added when the build-box track opens.

**Our edits never live in a submodule** — they are captured as re-appliable patch
files; see [`patches/README.md`](patches/README.md).

## Upstream repo SHAs — all git-confirmed 2026-06-03

| Repo | Role | Branch | Pinned SHA | Status |
|---|---|---|---|---|
| `OldRepoPreservation/mpeg2fpga` | MPEG-2 decoder RTL (BSD; Verilog; the sim target) | `master` | `2d51ffc21d5a8372cbfaa6be9dfde4a5b863245a` | **VENDORED now** → `core/mpeg2fpga` |
| `mrchrisster/MiSTer_MPEG2` | Cyclone V port we fork (`mpg_streamer.sv`, `mem_shim.sv`) | `main` | `11d1aa2d11649d0c4048d1b33fb1d2abc84771ef` | **VENDORED now** → `core/MiSTer_MPEG2` |
| `MiSTer-devel/CDi_MiSTer` | Flow template; MPEG-1 FMV reference | `main` | `ae583cd049a31cd44a10f6b744898275363a4ac2` | pin-confirmed; vendor when build-box track opens |
| `MiSTer-devel/Template_MiSTer` | Stock `sys/` framework (target `5CSEBA6U23I7`) | `master` | `f35083f3b40d24853abea4cd3f77caccbd71d5de` | pin-confirmed; vendor with build-box |
| `MiSTer-devel/Main_MiSTer` | `hps_io.sv` / `user_io.cpp` / `fpga_io.cpp` reference | `master` | `1f5337ee2cba8c62c361b1c044a2966b75c9ac67` | pin-confirmed; vendor with build-box |
| `mrchrisster/mister_cdi_vcd_creator` | Mux-side reference (MPEG-1 VCD) | `main` | `343d4e246f657058432aaa65612a95c263c6c3fc` | pin-confirmed; vendor with build-box |

### Verification note (supersedes `docs/session-bootstrap.md` §4 caveat)

All six SHAs above were confirmed with **`git ls-remote`** on **2026-06-03**, not read
off a GitHub commit page. This **supersedes the page-parse caveat** in
[`docs/session-bootstrap.md`](../docs/session-bootstrap.md) §4 ("read off the GitHub
commit pages via an automated page-parse… not confirmed with `git`"): the pins are now
git-confirmed ground truth.

- For the two **vendored** repos the staged gitlink equals the pin
  (`git ls-files -s core/mpeg2fpga core/MiSTer_MPEG2`), and each submodule's
  `git rev-parse HEAD` equals the pin.
- For all six, the listed **branch head** equalled the pin at confirmation time
  (`git ls-remote <url> <branch>`). The four not-yet-vendored repos may advance on
  their branch later; when vendored, pin the SHA actually checked out and update this
  table in the same change.

## Tool versions (this environment, 2026-06-03)

| Tool | Version | Command |
|---|---|---|
| Verilator | `Verilator 5.048 2026-04-26 rev vUNKNOWN-built20260426` | `verilator --version` |
| ffmpeg | `ffmpeg version 7.1.1 Copyright (c) 2000-2025 the FFmpeg developers` | `ffmpeg -version` (first line) |
| python3 | `Python 3.14.0` | `python3 --version` |

> The FPGA build toolchain (Quartus Prime Lite **17.0.x** via Docker
> `raetro/quartus:17.0`) runs on the **build box**, not this container; pin its image
> tag/digest there at M0 per `docs/session-bootstrap.md` §4.

## Re-clone / re-pin procedure

```sh
# fresh checkout of all vendored submodules at their pins
git submodule update --init --recursive

# confirm a vendored submodule is at its pin
git -C core/mpeg2fpga    rev-parse HEAD   # == 2d51ffc21d5a8372cbfaa6be9dfde4a5b863245a
git -C core/MiSTer_MPEG2 rev-parse HEAD   # == 11d1aa2d11649d0c4048d1b33fb1d2abc84771ef

# re-apply our edits (see patches/README.md)
git -C core/<name> apply ../patches/<name>-<topic>.patch
```
