# Morning HW decode-on-hardware diagnostic plan

**Purpose.** When the user is back at the bench, this is the decisive, ordered procedure to
**localize the decode-on-HW black screen** to one of four stages — feed, decoder/FIFO,
mem_shim, or video-out — using the fork's already-wired `uart_debug` telemetry, then pick
the candidate `.rbf` to build/load first.

This plan is informed by an overnight sim campaign (see "What the overnight sim proved"
below). Net: **the mem_shim ADDR_ERR same-cycle response collision is a proven RTL data-
corruption hazard with a sim-validated fix; the dual-clock Gray FIFO decodes BIT-IDENTICALLY
to the proven sim FIFO and is therefore NOT a functional black cause** (only a possible
physical/timing one). So the first candidate build carries the ADDR_ERR fix, not the FIFO swap.

---

## 0. Acquire the hardware lock and reload

MiSTer is single-tenant; grab the devlock before touching the core.

```sh
ssh mister
# command channel (FIFO). Small command set: load_core, Mount, Reset, screenshot.
echo "load_core /media/fat/_Console/NetVOB.rbf" > /dev/MiSTer_cmd     # adjust path
# For THIS core use the sd_* (Mount) feed path, NOT .mra/ioctl:
echo "Mount /media/fat/clips/test.mpg" > /dev/MiSTer_cmd              # mounts the PS clip
```

Then capture a **filmstrip** (a burst of screenshots), never a single shot — a black single
shot is ambiguous (could be a between-fields capture):

```sh
for i in $(seq 1 8); do echo screenshot > /dev/MiSTer_cmd; sleep 1; done
# PNGs land in /media/fat/screenshots/ ; scp them back.
```

---

## 1. Read the fork's `uart_debug` telemetry — the primary localizer

The fork instantiates `uart_debug` (`core/MiSTer_MPEG2/rtl/uart_debug.sv`) on `clk_sys`,
driving **`UART_TXD` at 115200 baud, 8N1**. It emits a repeating one-line status string. This
is the single richest localization signal and **every field needed to localize the black is in
it** — feed counters, decoder state, mem_shim counters, and a frame counter.

### 1a. How to read it

- **Physical UART (most reliable):** the DE10-Nano/SuperStation exposes the FPGA UART on the
  GPIO/USB-UART. Attach a 3.3 V USB-TTL adapter to `UART_TXD`/GND and:
  ```sh
  # from any host with the adapter:
  screen /dev/tty.usbserial-XXXX 115200      # or: picocom -b 115200 /dev/ttyUSB0
  ```
- **From the HPS over ssh, IF the UART is bridged to the ARM:** on stock MiSTer the FPGA UART
  is usually routed to the ARM serial. Try, on the target:
  ```sh
  ssh mister
  stty -F /dev/ttyS1 115200 raw -echo 2>/dev/null   # try ttyS1, ttyS0, ttyAMA0
  timeout 5 cat /dev/ttyS1                            # NOTE: real busybox timeout exists on-target
  ```
  If no device streams the line, fall back to the physical adapter (1a) — do not assume the
  bridge exists; **verify which `/dev/tty*` actually carries it before trusting a silent read.**
- **LEDs (coarse fallback, no UART):** `emu.sv` drives
  `LED_DISK = {streamer_active, stream_valid}` and a `heartbeat`. So the two disk LEDs tell you
  feed-alive / data-flowing at a glance even with no serial.

### 1b. The status line and its fields

Format string (`uart_debug.sv:96`):

```
L:x A:x B:x V:x X:xxx Y:xxx I:xxx S:x F:x E:x Q:x R:x M:x U:x K:x T:x D:x C:x H:x
   W:xxxx P:xxxx J:xxxx Z:xxxx @:xxxxxxxx G:x O:x N:x SC:x FC:xxxx RP:xxxx PC:xxxx
```

Decode of the fields that matter for localization (all hex):

| Field | Meaning | Source |
|------|---------|--------|
| `L` | PLL locked | `locked` |
| `T` | **streamer_active** (feed FSM running) | `mpg_streamer.debug_active` |
| `D` `C` `H` | sd_rd / sd_ack / cache_has_data (feed handshake) | streamer |
| `V` | **stream_valid** (a byte is being fed to the decoder this cycle) | `stream_valid` |
| `J` | **next_lba** (sector cursor — should ADVANCE if feed is alive) | `streamer_next_lba` |
| `B` | core busy (decoder backpressuring the feed) | `core_busy` |
| `Q` `E` | mem_req_valid / mem_req_en (decoder asking memory) | mpeg2video |
| `M` | **shim_state** `{cmd[1:0],saved_valid,state}` | `mem_shim.debug_state` |
| `U` `K` | sdram_busy(waitrequest) / sdram_ack | mem_shim |
| `P` | **mem_rd_count** (DDR3 reads accepted) | `mem_shim.debug_rd_count` |
| `W` | **mem_wr_count** (DDR3 writes accepted) | `mem_shim.debug_wr_count` |
| `RP` | **mem_rsp_count** (readdatavalid pulses received) | `mem_shim.debug_rsp_count` |
| `G` | **vld_err** (VLD decoder error flag) | `vld_err` |
| `O` | watchdog_rst latched (decoder watchdog fired) | `watchdog_rst` |
| `N` | vsync edges seen before "active" (0–7) | `core_vs_edge_cnt` |
| `FC` | **core_frame_cnt** (free-running vsync frame counter) | `core_frame_cnt` |

Take **two readings ~2–3 s apart** so you can see which counters are *advancing* vs *stuck*.

---

## 2. Decision tree (localize the stage)

Read the line, sample twice, and walk this tree top-down:

```
(A) FEED?      T==0  (streamer never active)              -> FEED bug: mount/reset/mpg_streamer.
               OR J (next_lba) NOT advancing AND V never 1 -> FEED bug (no bytes reaching decoder).
               [also check: H cache_has_data, D/C sd handshake]
        |
        v (feed is alive: T==1, J advancing, V toggling)
(B) DECODER?   P (mem_rd_count) NOT advancing  -> decoder never issues reads: VLD/parse stuck.
               OR G==1 (vld_err) / O==1 (watchdog) -> decoder error/hang (FIFO or parse).
               OR FC (frame_cnt) stays 0 with P advancing -> decode runs but never completes a
                  picture: classic decoder/FIFO desync.   [this is where a FIFO problem shows]
        |
        v (decoder issues reads & makes progress: P advancing)
(C) MEM_SHIM?  P advancing but RP (rsp_count) LAGS / STALLS behind P  -> response desync:
                  reads accepted, responses not returning 1:1.  THE ADDR_ERR COLLISION lives here
                  (a real readdatavalid dropped/overwritten by a synthetic ADDR_ERR-read response).
               Also: M stuck (shim_state frozen), U stuck high (waitrequest wedged),
                  SC!=0 with state frozen (skid wedged).
        |
        v (P and RP track 1:1, FC advancing)
(D) VIDEO-OUT? All counters healthy, FC advancing, but screen BLACK
                  -> video-out / analog path: VGA_* timing, ce_pix, VGA_F1 field parity,
                     ADV7125 DAC config, sync polarity, or 480i modeline.  Decode is fine;
                     the raster isn't reaching the DAC correctly.
```

### Quick interpretation cheatsheet
- `T==0` → feed/mount/reset. Re-check `.mgl`/`Mount` ordering and the latch-once `active`
  vulnerability (a `reset_n` glitch after mount permanently starves the feed — see
  `mpg_streamer.sv:85,99` and the feed-hardening note in the [feed+ADDR_ERR] analysis).
- `P` climbs, `RP` falls behind → **mem_shim response desync → build the ADDR_ERR-fix `.rbf`** (§4).
- `FC` climbs but black → it's **video-out**, not decode. Stop chasing the decoder.

---

## 3. What the overnight sim proved (so you don't re-litigate it)

Harness: `core/sim/memshim/` — the real `mpeg2fpga` decoder driving the **real `mem_shim.sv`**
through a behavioral Avalon/f2sdram DDR3 model (`ddr3_model.v`), at the true 27/108 MHz CDC
ratio. The decoder's own tag-sync `$stop` (`framestore_response.v:248`, compiled in via
`-D__IVERILOG__ ⇒ CHECK`) is live, so any response/tag desync hard-traps.

1. **Conformant baseline:** decodes greyramp clean through `mem_shim` (2 framestore frames,
   0 desync) — control passes.
2. **Non-conformant DDR3 modes** (added this session to `ddr3_model.v`):
   - `+ddr_drop=N` (drop every Nth readdatavalid): **REPRODUCES a hang** — the framestore
     stalls forever waiting for the missing response (watchdog NO-PROGRESS, only 1 frame).
     This is the cleanest model of "a lost response → black/hang".
   - `+ddr_dup=N` (extra readdatavalid): **REPRODUCES a hang** — surplus responses
     desync the stream (rsp_count overruns rd; watchdog fires).
   - `+ddr_reorder=N` (out-of-order responses): **does NOT reproduce** — the design is
     single-outstanding, so two responses are essentially never simultaneously ready;
     reorder is a no-op. Honest negative.
   - `+ddr_late_after_reset=M` (response strobe comes up late): **does NOT reproduce** —
     the decoder tolerates a delayed memory start as long as no response is later lost.
3. **ADDR_ERR same-cycle collision** — the headline result. A directed unit test
   (`tb_addrerr.v`, drives the real `mem_shim` with a real read response landing on the exact
   cycle `mem_shim` processes an ADDR_ERR read):
   - **UNFIXED `mem_shim`:** the real DDR3 data is **OVERWRITTEN with synthetic `0`**
     (`responses=2 real=0 zero=2`, FAIL). The collision is real and reachable; on HW a
     zeroed reference-frame fetch → garbage/black.
   - **FIXED `mem_shim`** (the ADDR_ERR-fix patch): **real data preserved, correct order**
     (`responses=2 real=1 zero=1`, PASS). Lone-ADDR_ERR control also passes.
   - Caveat: in the **full** decoder co-sim greyramp never naturally lands the same-cycle
     collision (0 collisions across runs). So the hazard is **proven and reachable but
     data-dependent** — it needs a real ADDR_ERR read (decoder overflow/flush) to coincide
     with a real readdatavalid. It is a genuine code defect worth fixing regardless.
4. **Dual-clock Gray FIFO** — the [FIFO build] suspicion. Swapped the bench's proven
   `generic_fifo_dc` for the **fork's hand-written Gray-code `xilinx_fifo_dc.v`** (the FIFO
   that actually synthesizes on HW for the 3 dual-clock instances) and re-ran the full decode:
   - **Frame 0 is BIT-IDENTICAL** to the generic-FIFO decode (54,192,739 bytes, exact match),
     0 desync, 0 `$stop`. **The Gray FIFO is functionally correct in sim.**
   - ⇒ The Gray FIFO is **not** a functional black cause. Any residual risk is purely
     **physical** (Quartus STA / CDC metastability / fitter) which Verilator cannot see.
     Demote the FIFO swap below the ADDR_ERR fix.

Repro commands (all in `core/sim/memshim/`):
```sh
make build                                            # full co-sim (uses patched mem_shim)
./run_memshim.sh run_drop   2 200 -- +ddr_rd_latency=8 +ddr_drop=37     # hang repro
./run_memshim.sh run_dup    2 200 -- +ddr_rd_latency=8 +ddr_dup=37      # hang repro
# directed ADDR_ERR collision A/B:
verilator --binary --timing -sv +incdir+../incdir -D__IVERILOG__ \
  --top-module tb_addrerr -Mdir obj_addrerr tb_addrerr.v ../../MiSTer_MPEG2/rtl/mem_shim.sv
./obj_addrerr/Vtb_addrerr +scenario=collide     # PASS w/ fix applied, FAIL w/o
# Gray-FIFO decisive decode:
make build FIFO_GRAY=1 OBJDIR=obj_gray BIN=obj_gray/Vtb_memshim
```

---

## 4. Which `.rbf` to build + load first

**Build #1 (DO THIS FIRST): ADDR_ERR-fix candidate.** Apply only the ADDR_ERR collision fix
(plus the existing pinned 480i/modeline patches already in the working tree). This is the
sim-proven, minimal, highest-confidence RTL fix and it costs nothing on the working path
(regression decode is unchanged, incl. the harsh latency/wait/jitter profile).

```sh
# apply the fix into the (pinned) submodule working tree — DO NOT commit into the submodule
git -C core/MiSTer_MPEG2 apply core/patches/hw/mpeg2fpga-memshim-addrerr-fix.patch
# build detached on the x86 build box (Quartus 17.0; ~30 min, single-thread bound):
ssh box 'setsid nohup bash -lc "cd <repo>/core/MiSTer_MPEG2 && \
  docker run --rm -v \"$PWD\":/work -w /work --entrypoint quartus_sh raetro/quartus:17.0 \
  --flow compile mpeg2fpga" > /tmp/build_addrerr.log 2>&1 &'
# revision = mpeg2fpga, TOP_LEVEL_ENTITY = sys_top -> output_files/mpeg2fpga.rbf
```
Load it (§0), `Mount` the clip, filmstrip, and re-read the UART line. **Expected effect:** if
the black was the mem_shim response desync, `RP` now tracks `P` and `FC` starts advancing.

**Build #2 (only if Build #1 still black AND the tree localizes to (B/C) with a FIFO smell):**
the Gray→generic dual-clock FIFO swap. Sim says it changes nothing functionally, so its only
value is eliminating a **physical/timing** suspect. Build it as a *separate* candidate so you
can A/B against Build #1 on HW.
```sh
git -C core/MiSTer_MPEG2 apply core/patches/hw/mpeg2fpga-fifo-dualclock-use-generic-fifo-dc.patch
# same detached Quartus invocation as above
```
After Build #2, check the Quartus **timing report** (STA / `Fmax` on the `clk_mem`/`clk_sys`
CDC paths and any unconstrained-CDC warnings) — that is where a Gray-FIFO problem would show,
not in the functional log.

**Do NOT** lead with the FIFO swap. Sim evidence puts the ADDR_ERR fix first.

---

## 5. If the tree points at FEED (T==0) or VIDEO-OUT (FC advancing, still black)

- **FEED:** no `.rbf` rebuild needed first — re-check the `Mount`/reset ordering and whether
  `reset_n` glitches after mount (the latch-once `active`). If confirmed, the feed-hardening
  edit (re-arm `active` on a sticky `mounted` latch in `emu.sv`) is the next patch to author.
  It is **not** yet written (lower confidence as the black cause; see blockers).
- **VIDEO-OUT:** decode is fine; pivot entirely to `VGA_*` (sync polarity is active-high per
  `emu.sv`), `VGA_F1 = ~core_v_pos[0]` field parity, `ce_pix` (13.5 MHz), the 480i modeline,
  and ADV7125 DAC config. A `screenshot` reads the framebuffer/HDMI path; if `screenshot` shows
  a picture but the **CRT** is black, suspect the analog DAC/modeline specifically.
```
