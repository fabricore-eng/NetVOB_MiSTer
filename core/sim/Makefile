# core/sim/Makefile — Verilator harness for the vendored mpeg2fpga decoder.
#
# This drives Verilator (5.x) against the *iverilog bench* configuration of
# Koen De Vleeschauwer's mpeg2fpga (core/mpeg2fpga), which is the soft-FIFO /
# behavioral-RAM simulation path (NO Xilinx primitives). It decodes an MPEG-2
# elementary stream and dumps:
#   - tv_out_NNNN.ppm   : the RGB raster as it would clock out of syncgen->DAC
#   - framestore_NNNN.ppm: the decoded YCbCr frame buffers (Y/Cr/Cb planes)
# A PNG is rendered from tv_out_0001.ppm for the verification ladder.
#
# Verification ladder targets:
#   make lint      -> verilator --lint-only            (rung: lint)
#   make build     -> verilator --binary -> Vtestbench (rung: compiles)
#   make stream.dat-> prepare the $readmemh hex input
#   make run       -> run the sim, produce PPMs         (rung: runs)
#   make png       -> render tv_out_0001.ppm to PNG     (rung: frame-png)
#   make all       -> build + run + png
#   make clean
#
# Why these flags (each is load-bearing — see README.md "Blockers resolved"):
#   --language 1364-2001 : the 2007 RTL uses `do` (data-out) as a signal name,
#                          which is a SystemVerilog keyword. Parse as Verilog-2001.
#   --timing             : the bench uses #delays (#1/#2) and clock generators.
#   +incdir+$(INCDIR)    : point at core/sim/incdir/ (symlinks to ONLY the
#                          `include "*.v" header files). Do NOT add rtl/mpeg2 to
#                          the module search path: it contains a SECOND set of
#                          fifo_sc/fifo_dc (in rtl/mpeg2/wrappers.v) that pull in
#                          Xilinx FIFO36/FIFO144 primitives. The bench's own
#                          wrappers.v (soft FIFOs) must win; isolating the incdir
#                          prevents Verilator auto-loading the Xilinx variants.
#   -DMODELINE_PAL_INTERL: the shipped greyramp.mpg is 720x576 PAL interlaced;
#                          this picks the matching output raster. (Use
#                          -DMODELINE_SIF for 352x288 progressive sources, or add
#                          an NTSC 480i modeline later for the project target.)

MPEG2    := $(abspath ../mpeg2fpga)
BENCH    := $(MPEG2)/bench/iverilog
RTL      := $(MPEG2)/rtl/mpeg2
INCDIR   := $(abspath ./incdir)
OBJDIR   := obj_dir
BIN      := $(OBJDIR)/Vtestbench

# Re-appliable submodule patches (the durable artifact; the gitlink stays pinned).
# A FRESH submodule checkout is pristine at the pin, so the build auto-applies
# EVERY core/patches/*.patch before linting/compiling. The `patch` target loops
# over them with idempotent reverse-check/apply/skip logic so a fresh checkout
# applies all of them and re-running is a no-op. Patches are applied in sorted
# order (shell glob); name them so any inter-patch ordering is lexicographic.
# Without these Verilator dies on a duplicate `fp` decl in mem_ctl.v (bench-fix),
# and the framestore PPM/log would lack picture_coding_type (pictype-probe).
# See core/patches/README.md.
PATCHDIR := $(abspath ../patches)
PATCHES  := $(sort $(wildcard $(PATCHDIR)/*.patch))

# Output raster modeline (see header). greyramp.mpg => PAL interlaced.
MODELINE ?= MODELINE_PAL_INTERL
# Source elementary stream (default: shipped greyramp.mpg, MP@ML 720x576i).
STREAM   ?= $(MPEG2)/tools/streams/greyramp.mpg

# Bench infrastructure (soft FIFO / behavioral RAM path).
BENCH_SRCS := \
  $(BENCH)/testbench.v \
  $(BENCH)/mem_ctl.v \
  $(BENCH)/wrappers.v \
  $(BENCH)/generic_fifo_dc.v \
  $(BENCH)/generic_fifo_sc_b.v \
  $(BENCH)/generic_dpram.v

# Decoder RTL (the exact list from bench/iverilog/Makefile SRCS,
# EXCLUDING rtl/mpeg2/wrappers.v and the xilinx_fifo*.v family).
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

SRCS := $(BENCH_SRCS) $(RTL_SRCS)

VFLAGS := --timing --language 1364-2001 \
  -Wno-fatal --Wno-MODDUP --Wno-WIDTHEXPAND --Wno-WIDTHTRUNC \
  --Wno-CASEX --Wno-COMBDLY --Wno-INITIALDLY \
  +incdir+$(INCDIR) \
  -D__IVERILOG__ -D$(MODELINE) \
  --top-module testbench

.PHONY: all lint build run png clean patch

all: png

# Idempotent: for EACH core/patches/*.patch, apply iff a clean checkout needs it;
# skip if already applied; fail loudly if a patch is stale (e.g. after a submodule
# pin bump). Patches are applied in sorted order so a multi-patch chain that edits
# the same file (e.g. bench-fix then pictype-probe, both touch mem_ctl.v) applies
# in the order the later patch's context was generated against.
patch:
	@for p in $(PATCHES); do \
	  name=$$(basename $$p); \
	  if git -C $(MPEG2) apply --reverse --check $$p 2>/dev/null; then \
	    echo "[patch] $$name already applied"; \
	  elif git -C $(MPEG2) apply --check $$p 2>/dev/null; then \
	    git -C $(MPEG2) apply $$p && echo "[patch] applied $$name"; \
	  else \
	    echo "[patch] ERROR: $$name neither applies cleanly nor is already applied — check submodule state/pin"; exit 1; \
	  fi; \
	done

lint: patch
	verilator --lint-only $(VFLAGS) $(SRCS)

build $(BIN): patch
	verilator --binary $(VFLAGS) -j 0 -O0 -Mdir $(OBJDIR) $(SRCS)

stream.dat:
	./prep_stream.sh "$(STREAM)"

# Run the sim. The upstream testbench has no end-of-stream $finish (it free-runs
# after the clip drains), so we run it detached and stop it once frames exist.
# For interactive use just run obj_dir/Vtestbench in a scratch dir; see README.
# MIN_FRAMES=3: the testbench only pads+closes tv_out_NNNN.ppm when the *next*
# frame's v_sync opens the following file. So we need >=3 opened files to be
# sure tv_out_0001.ppm (rendered by 'make png') is complete and not truncated.
run: $(BIN) stream.dat
	./run_sim.sh run 3 120

png: run
	ffmpeg -y -loglevel error -i run/tv_out_0001.ppm run/tv_out_0001.png
	@echo "wrote run/tv_out_0001.png"; file run/tv_out_0001.png

clean:
	rm -rf $(OBJDIR) run* stream.dat
	@echo "[clean] kept artifacts/ (durable evidence PNGs)"
