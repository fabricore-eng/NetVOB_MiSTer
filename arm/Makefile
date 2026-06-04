# arm/ — host-testable PS demuxer core.
#
# Off-target only: plain C11, no FPGA/MiSTer headers. `make test` builds and
# runs the unit test with strict warnings treated as errors.

CC      ?= cc
CFLAGS  ?= -std=c11 -Wall -Wextra -O2
BUILD   := build

.PHONY: all test clean

all: $(BUILD)/test_ps_demux

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/test_ps_demux: ps_demux.c ps_demux.h tests/test_ps_demux.c | $(BUILD)
	$(CC) $(CFLAGS) -o $@ ps_demux.c tests/test_ps_demux.c

test: $(BUILD)/test_ps_demux
	./$(BUILD)/test_ps_demux

clean:
	rm -rf $(BUILD)
