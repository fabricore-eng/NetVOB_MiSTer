# arm/ — host-testable ingest core (off-target).
#
# Off-target only: plain C11, no FPGA/MiSTer headers. `make test` builds and
# runs ALL unit tests with strict warnings treated as errors.
#
# Components under test:
#   ps_demux   — streaming MPEG-2 PS demux (video ES + audio ES routing)
#   ringbuf    — SPSC byte ring (the §4 ARM ring between TCP and the sd_* pull)
#   feeder     — model of the sd_* sector-pull backpressure / pull pacing

CC      ?= cc
CFLAGS  ?= -std=c11 -Wall -Wextra -Werror -O2
BUILD   := build

TESTS := \
	$(BUILD)/test_ps_demux \
	$(BUILD)/test_ps_demux_audio \
	$(BUILD)/test_ringbuf \
	$(BUILD)/test_feeder \
	$(BUILD)/test_realpipe \
	$(BUILD)/test_netingest

.PHONY: all test realpipe netd clean

all: $(TESTS)

$(BUILD):
	mkdir -p $(BUILD)

# ps_demux tests link ps_demux.c
$(BUILD)/test_ps_demux: ps_demux.c ps_demux.h tests/test_ps_demux.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ ps_demux.c tests/test_ps_demux.c

$(BUILD)/test_ps_demux_audio: ps_demux.c ps_demux.h tests/test_ps_demux_audio.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ ps_demux.c tests/test_ps_demux_audio.c

# ringbuf test links ringbuf.c
$(BUILD)/test_ringbuf: ringbuf.c ringbuf.h tests/test_ringbuf.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ ringbuf.c tests/test_ringbuf.c

# feeder test links feeder.c + ringbuf.c
$(BUILD)/test_feeder: feeder.c feeder.h ringbuf.c ringbuf.h tests/test_feeder.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ feeder.c ringbuf.c tests/test_feeder.c

# real-pipeline test: links the realpipe engine (tools/realpipe.c, WITHOUT
# REALPIPE_MAIN) + the three components. Part 1 (synthetic) always runs; part 2
# (real fixture) SKIPS cleanly if the local VOB is absent — so a clean clone / CI
# passes with no test data. Override the fixture path with NETVOB_REAL_VOB.
$(BUILD)/test_realpipe: tools/realpipe.c tools/realpipe.h ps_demux.c ps_demux.h \
                        ringbuf.c ringbuf.h feeder.c feeder.h tests/test_realpipe.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ tools/realpipe.c ps_demux.c ringbuf.c feeder.c tests/test_realpipe.c

# Developer harness (NOT part of `test`): standalone CLI that runs the pipeline
# on a VOB path you supply and prints the full report. Build with:
#   make -C arm realpipe   then   ./build/realpipe <stream.vob> [out.es]
realpipe: $(BUILD)/realpipe
$(BUILD)/realpipe: tools/realpipe.c tools/realpipe.h ps_demux.c ps_demux.h \
                   ringbuf.c ringbuf.h feeder.c feeder.h | $(BUILD)
	$(CC) $(CFLAGS) -DREALPIPE_MAIN -o $@ tools/realpipe.c ps_demux.c ringbuf.c feeder.c

# netingest test links netingest.c + ps_demux.c + ringbuf.c (the live-spine glue)
$(BUILD)/test_netingest: netingest.c netingest.h ps_demux.c ps_demux.h \
                         ringbuf.c ringbuf.h tests/test_netingest.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ netingest.c ps_demux.c ringbuf.c tests/test_netingest.c

# netd — the runnable TCP ingest daemon (connects to the Pi streamer, recv()s the
# PS, feeds the sd_* seam). Build with: make -C arm netd   then ./build/netd ...
netd: $(BUILD)/netd
$(BUILD)/netd: netd.c netingest.c netingest.h ps_demux.c ps_demux.h \
               ringbuf.c ringbuf.h | $(BUILD)
	$(CC) $(CFLAGS) -o $@ netd.c netingest.c ps_demux.c ringbuf.c

test: $(TESTS)
	@echo "== test_ps_demux ==";       ./$(BUILD)/test_ps_demux
	@echo "== test_ps_demux_audio =="; ./$(BUILD)/test_ps_demux_audio
	@echo "== test_ringbuf ==";        ./$(BUILD)/test_ringbuf
	@echo "== test_feeder ==";         ./$(BUILD)/test_feeder
	@echo "== test_realpipe ==";       ./$(BUILD)/test_realpipe
	@echo "== test_netingest ==";      ./$(BUILD)/test_netingest
	@echo "== ALL ARM HOST TESTS PASSED =="

clean:
	rm -rf $(BUILD)
