# core/sim/memshim/Makefile — Verilator co-sim: decoder THROUGH the real mem_shim.
#
# This harness reuses the proven core/sim decode pipeline (pristine mpeg2fpga
# RTL + bench SOFT FIFOs — the path that already decodes greyramp to a PNG) but
# REPLACES the bench mem_ctl.v with:
#   - core/MiSTer_MPEG2/rtl/mem_shim.sv   (the REAL Cyclone V shim under test)
#   - ddr3_model.v                        (a realistic Avalon-MM/f2sdram slave)
# and runs the REAL design clocking (clk=27, mem_clk=108, dot_clk=27) so the
# framestore CDC FIFOs see the true 27<->108 ratio.
#
# The ONLY changed variable vs the known-good core/sim run is the memory
# controller behind the framestore FIFOs, so any stall is attributable to it.
#
# Targets (verification ladder):
#   make lint   -> verilator --lint-only           (rung: compiles-ish)
#   make build  -> Vtb_memshim binary               (rung: compiles)
#   make stream.dat  -> reuse ../stream.dat (greyramp) or build from STREAM
#   make run    -> run the co-sim, produce PPMs + log (rung: runs)
#   make png    -> render a framestore Y plane to PNG (rung: frame-png)
#
# Knobs:
#   MODELINE=MODELINE_NTSC_INTERL (default) — MUST match the HW build, whose
#     core/MiSTer_MPEG2/rtl/mpeg2/modeline.v defaults to MODELINE_NTSC_INTERL
#     (720x480i, BT.601). The 720x480 NTSC test clips need NTSC display geometry:
#     under the old PAL default (576-line raster) the mixer's first-pixel-of-line
#     realign (mixer.v display_first_pixel) can never satisfy a stored ROW_0_COL_0,
#     so it parks in STATE_WAIT, backpressures resample->disp->addrgen, and PERMANENTLY
#     wedges the decoder at ~frame 4 — a SIM-CONFIG ARTIFACT the HW never sees. Keep this
#     matched to the stream/HW. (See docs handoff: display-ack-wedge = PAL-modeline artifact.)
#   DDR_LATENCY=8   DDR_WAIT=0   DDR_JITTER=0   (passed to ddr3_model via run_memshim.sh)

MPEG2    := $(abspath ../../mpeg2fpga)
BENCH    := $(MPEG2)/bench/iverilog
RTL      := $(MPEG2)/rtl/mpeg2
SHIM     := $(abspath ../../MiSTer_MPEG2/rtl)
INCDIR   := $(abspath ../incdir)
HERE     := $(abspath .)
OBJDIR   := obj_dir
BIN      := $(OBJDIR)/Vtb_memshim

PATCHDIR := $(abspath ../../patches)
PATCHES  := $(sort $(wildcard $(PATCHDIR)/*.patch))

MODELINE ?= MODELINE_NTSC_INTERL
STREAM   ?= $(MPEG2)/tools/streams/greyramp.mpg

# Bench infra: soft FIFOs + behavioral dual-clock CDC (NO Xilinx prims).
# NOTE: we DROP mem_ctl.v (replaced by ddr3_model.v + mem_shim.sv).
#
# FIFO_GRAY experiment: set FIFO_GRAY=1 to swap the dual-clock fifo_dc from the
# proven OpenCores generic_fifo_dc to the FORK's hand-written Gray-code
# xilinx_fifo_dc (the one that actually synthesizes on HW). This is the decisive
# [FIFO build] test — does the Gray FIFO break the framestore PNG / desync the
# decoder? wrappers_gray.v provides fifo_dc rewired to xilinx_fifo_dc; we still
# need generic_fifo_dc.v out and the fork's xilinx_fifo_dc.v in.
FIFO_GRAY ?= 0
ifeq ($(FIFO_GRAY),1)
BENCH_SRCS := \
  $(HERE)/wrappers_gray.v \
  $(abspath ../../MiSTer_MPEG2/rtl/mpeg2)/xilinx_fifo_dc.v \
  $(BENCH)/generic_fifo_sc_b.v \
  $(BENCH)/generic_dpram.v
else
BENCH_SRCS := \
  $(BENCH)/wrappers.v \
  $(BENCH)/generic_fifo_dc.v \
  $(BENCH)/generic_fifo_sc_b.v \
  $(BENCH)/generic_dpram.v
endif

# Decoder RTL (the pristine mpeg2fpga list — same as ../Makefile).
RTL_SRCS := \
  $(RTL)/mpeg2video.v $(RTL)/vbuf.v $(RTL)/getbits.v $(RTL)/vld.v \
  $(RTL)/rld.v $(RTL)/iquant.v $(RTL)/idct.v $(RTL)/motcomp.v \
  $(RTL)/motcomp_motvec.v $(RTL)/motcomp_addrgen.v $(RTL)/motcomp_picbuf.v \
  $(RTL)/motcomp_dcttype.v $(RTL)/motcomp_recon.v $(RTL)/fwft.v \
  $(RTL)/resample.v $(RTL)/resample_addrgen.v $(RTL)/resample_dta.v \
  $(RTL)/resample_bilinear.v $(RTL)/pixel_queue.v $(RTL)/mixer.v \
  $(RTL)/syncgen.v $(RTL)/syncgen_intf.v $(RTL)/yuv2rgb.v $(RTL)/osd.v \
  $(RTL)/regfile.v $(RTL)/reset.v $(RTL)/watchdog.v $(RTL)/framestore.v \
  $(RTL)/framestore_request.v $(RTL)/framestore_response.v $(RTL)/read_write.v \
  $(RTL)/mem_addr.v $(RTL)/synchronizer.v $(RTL)/probe.v $(RTL)/xfifo_sc.v

# Our harness + the unit under test. ddr3_model.v + tb are Verilog-2001;
# mem_shim.sv is SystemVerilog (parsed by .sv extension).
HARNESS_SRCS := $(HERE)/tb_memshim.v $(HERE)/ddr3_model.v $(HERE)/req_inject.v
UUT_SRCS     := $(SHIM)/mem_shim.sv

SRCS := $(HARNESS_SRCS) $(BENCH_SRCS) $(RTL_SRCS) $(UUT_SRCS)

# Verilator flags. We default the language to 1364-2001 for the 2007 RTL ('do'
# keyword etc.), but mem_shim.sv is picked up as SystemVerilog via its .sv
# extension. -Wno-fatal so loosely-typed 2007 RTL warnings don't abort.
VFLAGS := --timing --language 1364-2001 \
  -Wno-fatal --Wno-MODDUP --Wno-WIDTHEXPAND --Wno-WIDTHTRUNC \
  --Wno-CASEX --Wno-COMBDLY --Wno-INITIALDLY --Wno-CASEINCOMPLETE \
  --Wno-UNOPTFLAT --Wno-BLKANDNBLK --Wno-MULTIDRIVEN --Wno-LATCH \
  +incdir+$(INCDIR) \
  -D__IVERILOG__ -D$(MODELINE) \
  --top-module tb_memshim

.PHONY: all lint build run png clean patch

all: png

patch:
	@for p in $(PATCHES); do \
	  name=$$(basename $$p); \
	  if git -C $(MPEG2) apply --reverse --check $$p 2>/dev/null; then \
	    echo "[patch] $$name already applied"; \
	  elif git -C $(MPEG2) apply --check $$p 2>/dev/null; then \
	    git -C $(MPEG2) apply $$p && echo "[patch] applied $$name"; \
	  else \
	    echo "[patch] WARN: $$name neither applies nor reverses cleanly — continuing"; \
	  fi; \
	done

lint: patch
	verilator --lint-only $(VFLAGS) $(SRCS)

build $(BIN): patch
	verilator --binary $(VFLAGS) -j 0 -O0 -Mdir $(OBJDIR) $(SRCS)

stream.dat:
	@if [ -f ../stream.dat ]; then \
	  echo "[stream] reusing ../stream.dat"; cp -f ../stream.dat stream.dat; \
	else \
	  echo "[stream] building from $(STREAM)"; ../prep_stream.sh "$(STREAM)"; \
	fi

run: $(BIN) stream.dat
	./run_memshim.sh run

png: run
	@ls run/framestore_*.ppm >/dev/null 2>&1 && \
	  python3 ../framestore_extract.py $$(ls run/framestore_*.ppm | tail -1) run/yp --prefix fs && \
	  echo "wrote run/yp/*.png" || echo "no framestore PPMs produced (see run/run.log)"

clean:
	rm -rf $(OBJDIR) run* stream.dat
	@echo "[clean] done"
