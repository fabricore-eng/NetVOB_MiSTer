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
	$(BUILD)/test_feeder

.PHONY: all test clean

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

test: $(TESTS)
	@echo "== test_ps_demux ==";       ./$(BUILD)/test_ps_demux
	@echo "== test_ps_demux_audio =="; ./$(BUILD)/test_ps_demux_audio
	@echo "== test_ringbuf ==";        ./$(BUILD)/test_ringbuf
	@echo "== test_feeder ==";         ./$(BUILD)/test_feeder
	@echo "== ALL ARM HOST TESTS PASSED =="

clean:
	rm -rf $(BUILD)
