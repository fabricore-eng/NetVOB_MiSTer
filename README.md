# NetVOB_MiSTer

Play video on a CRT at native **480i with 24-bit color** by **hardware-decoding a
DVD-like MPEG-2 program stream inside an FPGA**. A provider service on a Raspberry
Pi 5 streams one MPEG-2 Program Stream over the network to a **SuperStation One**
(Cyclone V, MiSTer-compatible); the console ARM injects it into an FPGA MPEG-2
decoder core, and video leaves through the console's true 24-bit ADV7125 analog DAC.

The name nods to DVD's Video Object (`.VOB`) container — the same MPEG-2
program-stream payload, streamed over the network instead of read off a disc.

> **Status: planning.** This repo currently contains the project plan and design
> docs only — no implementation yet.

## Start here

- **[PLAN.md](PLAN.md)** — architecture, locked decisions, data-flow diagram, repo layout.
- **[docs/findings.md](docs/findings.md)** — verified prior art + the FPGA injection seam.
- **[docs/service-design.md](docs/service-design.md)** — Pi 5 service + source-plugin interface.
- **[docs/transport.md](docs/transport.md)** — wire protocol + buffering / flow-control / A-V sync.
- **[docs/catalog-browse.md](docs/catalog-browse.md)** — two-library browse, badging, NFC/Zaparoo, metadata.
- **[docs/milestones.md](docs/milestones.md)** — M0–M7.
- **[docs/risk-register.md](docs/risk-register.md)** — ranked risks.
- **[CLAUDE.md](CLAUDE.md)** — guidance for future dev/AI sessions.

## Two libraries, kept separate

1. **DVD Dumps** — *field-exact*. Reads your VOB/VIDEO_TS dumps and passes the
   program stream through losslessly (near-zero CPU). The spine.
2. **Plex** — *transcoded*. Pulls from Plex and transcodes to 480i MPEG-2 on the Pi.
