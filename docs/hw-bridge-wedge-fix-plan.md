# HW f2sdram bridge-wedge fix + raw-word-dump probe plan (2026-06-05)

Captured from the dvd/573/cockpit hub session so the next cycle executes without re-deriving.

## Why
The decode bug is localized to the **HW bitstream-delivery path** (mpg_streamer + mem_shim DDR
roundtrip): the bitstream word getbits reads (`vbr_rd_dta`) diverges HW-vs-sim from ~word 90
(pre-wedge partial read: HW BL=B1AA024E@0x5A vs sim c6f07360; HW VL=FFFFFE30@0x22D vs sim fffffde4).
vld/getbits/vbuf RTL is byte-identical, so the bug is in the HW-only DDR/ingest path.

To probe that path I must tap `vbr_rd_dta` — a **bridge-adjacent** net. Tapping it wedges the
marginal, placement-sensitive f2sdram bridge path (NOT congestion — core is 36% ALM). Two mitigations,
in order of cheapness:

## Step 1 (cheapest): source-register the tap (cockpit)
A LogicLock pins PLACEMENT, but routing 64 bridge-adjacent bits cross-chip to UART can STILL perturb
the marginal path. So **register the tap at its SOURCE**: a capture reg right at `vbr_rd_dta`, then the
long UART route runs from the REG (placeable anywhere), not the live bridge net.

```verilog
// in mpeg2video.v, parent level — capture reg adjacent to vbr_rd_dta:
reg [63:0] vbr_cap [0:3];          // first 4 bitstream words read out of DDR
reg [2:0]  vbr_cap_n;              // 0..4 (stops at 4)
always @(posedge clk)
  if (~sync_rst) vbr_cap_n <= 0;
  else if (vbr_rd_valid && vbr_cap_n < 4) begin
    vbr_cap[vbr_cap_n] <= vbr_rd_dta;   // <-- registered AT the source
    vbr_cap_n <= vbr_cap_n + 1;
  end
// then route vbr_cap[*] (registered) to emu->uart, NOT vbr_rd_dta directly.
```
Dump vbr_cap[0..3] as 64-bit hex over UART. Compare to the sim's first 4 `vbr_rd_dta` words.
If source-registering alone feeds clean, **no LogicLock needed**.

Verdict from the raw words:
- HW words are a **byte-permutation** of sim words => lane/endian ORDERING bug (fix mem_shim/vbuf
  byte-lane order, or the f2sdram 64-bit word order).
- HW word[0] already differs (not a permutation) => corruption from the start (address/collision).
- HW words 0..3 MATCH sim but BL diverged at ~90 => corruption starts mid-stream (later word), not at
  the start — capture words near index 90 next.

## ⛔ STEP 1 + STEP 2 RESULTS (2026-06-05)
- **Step 1 (source-register) FAILED:** the raw-dump still wedged (J:0021/W:0007/PC:A000, BN=0).
  cockpit's diagnosis: a capture reg fed by `vbr_rd_dta` is PLACED by its INPUT locality — the placer
  drops it in the bridge neighborhood to reach the source net, so decoupling the OUTPUT route treats
  the wrong half. The reg itself is the intruder.
- **Step 2 (LogicLock) BLOCKED — no license:** `raetro/quartus:17.0` is the free WEB EDITION.
  LogicLock needs a subscription → Quartus emits Warning 292013 + Critical Warning 140003 and SILENTLY
  REMOVES all LogicLock regions. 573's keywords were correct; the LICENSE blocks the feature. So
  LogicLock is unavailable for this toolchain. (Open Q for cockpit: does the free edition also block
  set_location_assignment / PARTITION-based floorplanning, or just LogicLock?)

## LICENSE-FREE NEXT OPTIONS (preferred order)
1. **getbits-OUTPUT probe (VLD region, away from the bridge).** Tap the effective bitstream vld
   decodes (`getbits[23:0]` + advance/align) instead of bridge-adjacent `vbr_rd_dta`. getbits sits in
   the vld region (one stage past the bridge-adjacent vbr fifo), so it should feed clean like the
   dct_coeff VLD probe did. First getbits should be the `000001b3` start code if the bitstream is
   intact, shuffled if not. This is the most promising — clean-probe-able AND shows the effective bits.
2. **CPU-side /dev/mem dump of the vbuf bitstream DDR region**, probe-free, on the CLEAN VLD-probe
   build (feeds the full clip, no wedge). Compare to the clip: match => write/storage clean, corruption
   is in the FPGA read path; mismatch => write-path corrupts (pattern shows ordering vs corruption).
   Needs the vbuf DDR base address (find in mem_addr.v / framestore allocation; framestore is 0x30000000).
3. **573's handoff register:** add a pipeline/register stage on MY side of the mem_shim↔bridge handoff
   to add hold margin so the marginal path stops mattering (no license needed). Fixes the WEDGE so any
   probe can run, but doesn't directly answer ordering-vs-corruption.

## Step 2 (SUPERSEDED — license-blocked): LogicLock-pin the bridge region (573)
573's snippet — UNVERIFIED in-tree (no Konami_System_573/psx qsf uses LogicLock; it's Quartus-17.0
knowledge). **Sanity-check the keyword spellings against the 17.0 handbook before trusting placement.**

```tcl
# --- LogicLock region: pin the HPS f2sdram bridge + mem_shim placement ---
set_global_assignment -name LL_ENABLED ON      -section_id f2sdram_ll
set_global_assignment -name LL_AUTO_SIZE ON    -section_id f2sdram_ll
set_global_assignment -name LL_STATE FLOATING  -section_id f2sdram_ll
set_global_assignment -name LL_RESERVED OFF    -section_id f2sdram_ll
# members — EXACT instance paths pulled from the 2026-06-05T14:15Z .fit.rpt by cockpit
# (pin the soft GLUE between mem_shim and the HPS bridge, NOT the hard Altera interface block
#  sysmem_lite:sysmem|sysmem_HPS_fpga_interfaces:fpga_interfaces — that's framework IP):
set_instance_assignment -name LL_MEMBER_OF f2sdram_ll -to emu:emu|mem_shim:mem_shim_inst -section_id f2sdram_ll
set_instance_assignment -name LL_MEMBER_OF f2sdram_ll -to emu:emu|mpeg2video:mpeg2video_inst|framestore:framestore|fifo_dc:mem_request_fifo|xilinx_fifo_dc:xfifo_dc -section_id f2sdram_ll
set_instance_assignment -name LL_MEMBER_OF f2sdram_ll -to emu:emu|mpeg2video:mpeg2video_inst|framestore:framestore|fifo_dc:mem_response_fifo|xilinx_fifo_dc:xfifo_dc -section_id f2sdram_ll
# the MiSTer f2sdram safe-terminator soft glue (3 ports; ram1/ram2=main DDR, vbuf=video buffer).
# Include all 3 for a first FLOATING build (safe default); vbuf is the bitstream-cache readback port:
set_instance_assignment -name LL_MEMBER_OF f2sdram_ll -to sysmem_lite:sysmem|f2sdram_safe_terminator:f2sdram_safe_terminator_ram1 -section_id f2sdram_ll
set_instance_assignment -name LL_MEMBER_OF f2sdram_ll -to sysmem_lite:sysmem|f2sdram_safe_terminator:f2sdram_safe_terminator_ram2 -section_id f2sdram_ll
set_instance_assignment -name LL_MEMBER_OF f2sdram_ll -to sysmem_lite:sysmem|f2sdram_safe_terminator:f2sdram_safe_terminator_vbuf -section_id f2sdram_ll
```
**CRITICAL workflow — FLOAT then LOCK** (a floating region alone won't stop the re-roll):
1. Build with FLOATING+AUTO_SIZE — groups the bridge logic so it moves as a UNIT. If that alone feeds
   clean, may be done.
2. If still re-rolling: read the region's resulting `LL_ORIGIN` + `LL_WIDTH`/`LL_HEIGHT` from that fit
   (Chip Planner / .fit.rpt), hard-set them, flip `LL_STATE`→`LOCKED`. Now nailed at a known-good spot.

## Step 2-alt: add hold margin on my side of the handoff (573)
The path is MARGINAL+placement-sensitive; bridge is Altera IP (can't add regs inside). A register/
pipeline stage on the mem_shim↔bridge handoff (the part we own) could add hold margin so placement
stops mattering. LogicLock is the cleaner first try.

## cockpit's build-side confirms (their lane) when a build lands
1. zero-churn wedge signature, 2. .fit.rpt that the LogicLock region ACTUALLY took (Quartus silently
drops a region it can't honor + just warns), 3. bridge hold slack before/after the pin. cockpit can
pull exact f2sdram/mem_shim instance paths from the .fit.rpt hierarchy for the region members.

## Sim side
Add a raw first-4-words dump of `testbench.mpeg2.vbr_rd_dta` (gated by vbr_rd_valid) to
bench/iverilog/testbench.v to get the sim golden words. Sim build: `make -o patch build` (stale patch).
