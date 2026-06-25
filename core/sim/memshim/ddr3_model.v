/*
 * ddr3_model.v — behavioral MiSTer f2sdram / DDRAM Avalon-MM slave model.
 *
 * Part of core/sim/memshim/: a Verilator co-sim that drives the mpeg2fpga
 * decoder through the REAL core/MiSTer_MPEG2/rtl/mem_shim.sv instead of the
 * bench mem_ctl.v, to reproduce / localize the hardware black-screen.
 *
 * This is the piece the prior static analyses said did NOT exist in-tree:
 * a realistic Avalon-MM/f2sdram slave with multi-cycle waitrequest and a
 * decoupled, N-cycle-later readdatavalid (the bench mem_ctl.v is a
 * zero-latency, never-stalling, one-response-per-read-the-next-cycle model
 * that structurally CANNOT exercise the hazardous mem_shim paths).
 *
 * Protocol modeled = the MiSTer "DDRAM" port (Intel HPS-to-FPGA SDRAM bridge
 * exposed through the MiSTer framework), which mem_shim.sv targets via emu.sv:
 *   - ddr3_waitrequest (= DDRAM_BUSY): when HIGH, the slave is NOT accepting a
 *     command this cycle. A read/write command is ACCEPTED on the first cycle
 *     where (ddr3_read | ddr3_write) && !ddr3_waitrequest.
 *   - ddr3_read / ddr3_write: command strobes (held by the master until accept).
 *   - ddr3_addr: 29-bit WORD address (Avalon word granularity at the 64-bit
 *     data width; mem_shim packs {7'b0011000, decoder_word_addr[21:0]}).
 *   - ddr3_burstcnt: burst length in words (mem_shim hardcodes 1).
 *   - ddr3_readdata / ddr3_readdatavalid: read RESPONSE, returned N cycles
 *     AFTER the read is accepted — decoupled from command acceptance. Writes
 *     produce NO readdatavalid (this matches the decoder tag-fifo contract:
 *     only reads yield responses).
 *
 * Knobs (Verilator +plusargs, all optional):
 *   +ddr_rd_latency=N    fixed read latency in clk cycles from accept->valid
 *                        (default 8). The realistic round-trip on HW.
 *   +ddr_wait_period=P   waitrequest is HIGH for (P-1) of every P cycles when a
 *                        command is pending (default 0 = never stall). Models a
 *                        bridge that only accepts 1 command per P cycles.
 *   +ddr_rd_jitter=J     add 0..J cycles of pseudo-random extra read latency
 *                        (default 0 = fixed latency).
 *   +ddr_zero_latency    force the zero-latency / never-stall behavior, i.e.
 *                        emulate the bench mem_ctl timing (control experiment).
 *
 * Backing store: a 64-bit-word memory array indexed by the DECODER word
 * address (ddr3_addr[21:0], i.e. the window-3 offset). This lets the framestore
 * dump task (reused verbatim from the bench mem_ctl.v) read decoded frames out
 * the same way, so we can produce a framestore PNG iff the decode succeeds
 * THROUGH mem_shim.
 *
 * Instrumentation (the whole point — localize the stall):
 *   - rd_issued / rd_responded counters and a per-event $display trace
 *     (gated by +ddr_trace) so we can see exactly when responses stop.
 *   - out-of-bound address flagging (decoder word addr > END_OF_MEM).
 *   - window-bits check (ddr3_addr[28:22] must be 7'b0011000).
 */

`include "timescale.v"

module ddr3_model (
    input             clk,        // mem_clk domain (108 MHz on HW)
    input             rst,        // active-HIGH reset (matches bench rst convention)

    // Avalon-MM / DDRAM slave side (driven by mem_shim master)
    input      [28:0] ddr3_addr,
    input       [7:0] ddr3_burstcnt,
    input             ddr3_read,
    input             ddr3_write,
    input      [63:0] ddr3_writedata,
    input       [7:0] ddr3_byteenable,
    output reg [63:0] ddr3_readdata,
    output reg        ddr3_readdatavalid,
    output            ddr3_waitrequest
);

`include "mem_codes.v"

  // ---------------------------------------------------------------------------
  // Backing store: 64-bit words, indexed by decoder word address (addr[21:0]).
  // Sized to END_OF_MEM (= ADDR_ERR = 0x1effff for MP@HL) like the bench mem.
  // ---------------------------------------------------------------------------
  reg [63:0] mem[0:END_OF_MEM];

  // ---------------------------------------------------------------------------
  // Knobs
  // ---------------------------------------------------------------------------
  integer rd_latency;
  integer wait_period;
  integer rd_jitter;
  integer zero_latency;
  integer do_trace;

  // -------------------------------------------------------------------------
  // NON-CONFORMANT (fault-injection) modes — the overnight repro knobs.
  // These deliberately VIOLATE the Avalon-MM read-response contract that
  // mpeg2fpga's mem controller assumes (exactly one in-order response per
  // accepted read, in request order). They exist to PROVE whether the
  // decoder's tag-checker (framestore_response.v:248 $stop, compiled in via
  // -D__IVERILOG__ => CHECK) catches a desync, i.e. to localize the HW black
  // to the memory-response path.
  //
  //   +ddr_reorder=N    swap the order of responses: hold a ready response and
  //                     emit the NEXT-ready one first, once every N drains.
  //                     N=0 disables. (out-of-order responses)
  //   +ddr_drop=N       silently DROP every Nth read response (the read was
  //                     accepted + counted in rd_issued, but no readdatavalid
  //                     is ever emitted). N=0 disables. (lost response)
  //   +ddr_dup=N        emit a DUPLICATE readdatavalid (same data, extra pulse)
  //                     every Nth response. N=0 disables. (extra response)
  //   +ddr_late_after_reset=M  after reset deasserts, SUPPRESS readdatavalid
  //                     for the first M mem_clk cycles even though reads are
  //                     accepted (models a bridge that comes up late / a
  //                     reset-deassert skew on the response strobe). M=0 off.
  // -------------------------------------------------------------------------
  integer mode_reorder;
  integer mode_drop;
  integer mode_dup;
  integer late_after_reset;
  integer lock_threshold;     // REALISTIC: the f2sdram bridge WEDGES (waitrequest stuck
                              // high, responses stop) once this many reads are outstanding
                              // (on-silicon lock-probe caught it locking at ~9 in flight).
                              // 0 = off. This models the REAL failure, not an artificial drop.
  integer gap_wedge;          // REALISTIC #2 (HW-confirmed 2026-06-25): the f2sdram needs the command
                              // pipeline MOVING to drain responses. If NO command is accepted for this
                              // many cycles WHILE reads are outstanding, the bridge WEDGES (waitrequest
                              // stuck, responses stop). This reproduces the throttle read-HOLD wedge:
                              // cap=4 AND cap=6 builds wedged @~84 reads (the hold gaps the command
                              // stream ~read-latency cycles); throttle-off ran 6000. 0 = off.

  // LFSR for pseudo-random jitter / waitrequest phase
  reg [31:0] lfsr;

  initial begin
    rd_latency   = 8;
    wait_period  = 0;
    rd_jitter    = 0;
    zero_latency = 0;
    do_trace     = 0;
    mode_reorder = 0;
    mode_drop    = 0;
    mode_dup     = 0;
    late_after_reset = 0;
    lock_threshold = 0;
    gap_wedge    = 0;
    lfsr         = 32'hACE1_2345;
    if ($value$plusargs("ddr_rd_latency=%d", rd_latency));
    if ($value$plusargs("ddr_wait_period=%d", wait_period));
    if ($value$plusargs("ddr_rd_jitter=%d", rd_jitter));
    if ($test$plusargs("ddr_zero_latency")) zero_latency = 1;
    if ($test$plusargs("ddr_trace")) do_trace = 1;
    if ($value$plusargs("ddr_reorder=%d", mode_reorder));
    if ($value$plusargs("ddr_drop=%d", mode_drop));
    if ($value$plusargs("ddr_dup=%d", mode_dup));
    if ($value$plusargs("ddr_late_after_reset=%d", late_after_reset));
    if ($value$plusargs("ddr_lock_threshold=%d", lock_threshold));
    if ($value$plusargs("ddr_gap_wedge=%d", gap_wedge));
    if (zero_latency) begin rd_latency = 0; wait_period = 0; rd_jitter = 0; end
    $display("[ddr3_model] rd_latency=%0d wait_period=%0d rd_jitter=%0d zero_latency=%0d",
             rd_latency, wait_period, rd_jitter, zero_latency);
    $display("[ddr3_model] NONCONFORMANT modes: reorder=%0d drop=%0d dup=%0d late_after_reset=%0d lock_threshold=%0d gap_wedge=%0d",
             mode_reorder, mode_drop, mode_dup, late_after_reset, lock_threshold, gap_wedge);
  end

  always @(posedge clk) lfsr <= {lfsr[30:0], lfsr[31]^lfsr[21]^lfsr[1]^lfsr[0]};

  // ---------------------------------------------------------------------------
  // waitrequest generation.
  //   wait_period==0  -> never stall (waitrequest low).
  //   wait_period==P  -> accept a command only when the free-running phase
  //                      counter is 0; busy otherwise. This makes the bridge
  //                      accept at most 1 command every P cycles, modelling a
  //                      slow, throttled f2sdram bus.
  // waitrequest is only meaningful while a command is asserted, but we drive it
  // unconditionally (Avalon legal) so the master sees realistic backpressure.
  // ---------------------------------------------------------------------------
  reg        locked;          // sticky over-issue lock (set in the always block below)
  reg [15:0] wait_phase;
  always @(posedge clk)
    if (rst) wait_phase <= 0;
    else if (wait_period <= 1) wait_phase <= 0;
    else if (wait_phase == (wait_period[15:0]-16'd1)) wait_phase <= 0;
    else wait_phase <= wait_phase + 16'd1;

  assign ddr3_waitrequest = locked ? 1'b1 :
                            (wait_period <= 1) ? 1'b0 : (wait_phase != 16'd0);

  wire cmd_accept_rd = ddr3_read  && !ddr3_waitrequest;
  wire cmd_accept_wr = ddr3_write && !ddr3_waitrequest;

  // ---------------------------------------------------------------------------
  // Read-response pipeline. A simple shift-register of pending responses keyed
  // by countdown. Single-outstanding is the common case (mem_shim serializes),
  // but we support a small depth so the model is not artificially in-lockstep.
  // Each accepted read schedules a response 'lat' cycles out with its data.
  // ---------------------------------------------------------------------------
  localparam PIPE = 64;            // max outstanding responses tracked
  reg        rsp_pending [0:PIPE-1];
  reg [31:0] rsp_countdown[0:PIPE-1];
  reg [63:0] rsp_data     [0:PIPE-1];
  integer    k;

  // Address bookkeeping for instrumentation
  wire [21:0] word_addr  = ddr3_addr[21:0];
  wire  [6:0] window     = ddr3_addr[28:22];
  reg         oob_seen;
  reg         badwin_seen;

  // counters
  reg [31:0] rd_issued;
  reg [31:0] rd_responded;
  reg [31:0] wr_issued;

  integer    cur_lat;

  // realistic over-issue lock instrumentation
  reg [31:0] outstanding_peak;          // max in-flight reads over the run
  wire [31:0] outstanding_now = rd_issued - rd_responded;

  // command-gap (pipeline-stall) wedge instrumentation
  reg [31:0] cmd_gap;                    // cycles since the last accepted command

  // Non-conformant bookkeeping
  reg [31:0] drain_count;        // # of drain opportunities (a response became ready)
  reg [31:0] post_reset_cycles;  // mem_clk cycles since reset deassert
  reg        dup_pending;        // emit a duplicate of last response next cycle
  reg [63:0] dup_data;
  reg [31:0] dropped;
  reg [31:0] duped;
  reg [31:0] reordered;

  always @(posedge clk) begin
    if (rst) begin
      ddr3_readdatavalid <= 1'b0;
      ddr3_readdata      <= 64'd0;
      rd_issued          <= 0;
      rd_responded       <= 0;
      wr_issued          <= 0;
      oob_seen           <= 1'b0;
      badwin_seen        <= 1'b0;
      drain_count        <= 0;
      post_reset_cycles  <= 0;
      dup_pending        <= 1'b0;
      dup_data           <= 64'd0;
      dropped            <= 0;
      duped              <= 0;
      reordered          <= 0;
      locked             <= 1'b0;
      outstanding_peak   <= 0;
      cmd_gap            <= 0;
      for (k = 0; k < PIPE; k = k + 1) begin
        rsp_pending[k]   <= 1'b0;
        rsp_countdown[k] <= 0;
        rsp_data[k]      <= 64'd0;
      end
    end
    else begin
      ddr3_readdatavalid <= 1'b0;   // default: no response this cycle

      // ---- REALISTIC over-issue lock (faithful to the HW failure, NOT a fake drop) ----
      // The HW f2sdram bridge wedges (waitrequest stuck high, responses stop) once too
      // many reads are outstanding (on-silicon probe: locked at 6). Sticky like HW.
      if (outstanding_now > outstanding_peak) begin
        outstanding_peak <= outstanding_now;
        $display("[ddr3_model %0t] peak in-flight reads = %0d", $time, outstanding_now);
        $fflush;
      end
      if (!locked && lock_threshold != 0 && outstanding_now >= lock_threshold[31:0]) begin
        locked <= 1'b1;
        $display("[ddr3_model %0t] *** LOCK: f2sdram wedged -- %0d reads outstanding >= threshold %0d (waitrequest stuck, responses stop) ***",
                 $time, outstanding_now, lock_threshold);
      end

      // ---- REALISTIC #2: command-gap (pipeline-stall) wedge (HW-confirmed) ----
      // The f2sdram needs the command pipeline MOVING to drain responses. If the master
      // stops issuing for gap_wedge cycles while reads are still in flight, the bridge
      // wedges. This reproduces the throttle read-HOLD wedge seen on silicon.
      if (cmd_accept_rd || cmd_accept_wr) cmd_gap <= 0;
      else if (!(&cmd_gap))               cmd_gap <= cmd_gap + 1;
      if (!locked && gap_wedge != 0 && outstanding_now > 0 && cmd_gap >= gap_wedge[31:0]) begin
        locked <= 1'b1;
        $display("[ddr3_model %0t] *** GAP-WEDGE: f2sdram wedged -- no command for %0d cycles (>= %0d) with %0d reads in flight (pipeline starved) ***",
                 $time, cmd_gap, gap_wedge, outstanding_now);
      end

      // ---- accept a WRITE ----
      if (cmd_accept_wr) begin
        wr_issued <= wr_issued + 1;
        if (window != 7'b0011000) badwin_seen <= 1'b1;
        if (word_addr > END_OF_MEM[21:0]) begin
          oob_seen <= 1'b1;
          if (do_trace) $display("[ddr3_model %0t] WR OOB word_addr=%h (> END_OF_MEM)", $time, word_addr);
        end else begin
          mem[word_addr] <= ddr3_writedata;
        end
        if (do_trace) $display("[ddr3_model %0t] WR accept addr=%h word=%h dta=%h", $time, ddr3_addr, word_addr, ddr3_writedata);
      end

      // ---- accept a READ: schedule a response ----
      if (cmd_accept_rd) begin
        rd_issued <= rd_issued + 1;
        if (window != 7'b0011000) badwin_seen <= 1'b1;
        // latency = base + optional jitter
        cur_lat = rd_latency;
        if (rd_jitter > 0) cur_lat = rd_latency + (lfsr % (rd_jitter+1));
        if (cur_lat < 1) cur_lat = 1;   // at least 1 cycle: response is registered
        // find a free pipe slot
        begin : alloc
          integer free_slot;
          free_slot = -1;
          for (k = 0; k < PIPE; k = k + 1)
            if (!rsp_pending[k] && free_slot < 0) free_slot = k;
          if (free_slot < 0) begin
            $display("[ddr3_model %0t] *** ERROR: response pipe overflow (>%0d outstanding) ***", $time, PIPE);
          end else begin
            rsp_pending[free_slot]   <= 1'b1;
            rsp_countdown[free_slot] <= cur_lat[31:0];
            if (word_addr > END_OF_MEM[21:0]) begin
              oob_seen <= 1'b1;
              rsp_data[free_slot] <= 64'd0;
              if (do_trace) $display("[ddr3_model %0t] RD OOB word_addr=%h -> 0", $time, word_addr);
            end else begin
              rsp_data[free_slot] <= mem[word_addr];
            end
            if (do_trace) $display("[ddr3_model %0t] RD accept addr=%h word=%h lat=%0d (issued=%0d)", $time, ddr3_addr, word_addr, cur_lat, rd_issued+1);
          end
        end
      end

      // ---- count post-reset cycles (for +ddr_late_after_reset) ----
      post_reset_cycles <= post_reset_cycles + 1;

      // ---- advance pending responses; emit at most one per cycle ----
      // Single-outstanding on HW so 'at most one ready per cycle' is the norm.
      //
      // Non-conformant injection happens HERE on the response path:
      //   late_after_reset : suppress ALL emission for the first M cycles
      //   dup              : a pending duplicate beats a fresh response
      //   reorder/drop     : decided when a slot first becomes "ready"
      begin : drain
        integer emitted;
        integer ready0;            // index of first (in-order) ready slot
        integer ready1;            // index of second ready slot (for reorder)
        integer chosen;
        emitted = 0;
        ready0  = -1;
        ready1  = -1;

        // 1) tick down all countdowns; collect up to two ready slots in order.
        for (k = 0; k < PIPE; k = k + 1) begin
          if (rsp_pending[k]) begin
            if (rsp_countdown[k] <= 1) begin
              if (ready0 < 0)      ready0 = k;
              else if (ready1 < 0) ready1 = k;
            end else begin
              rsp_countdown[k] <= rsp_countdown[k] - 1;
            end
          end
        end

        // 2) emit a pending DUPLICATE first (extra response, contract violation).
        if (dup_pending && !locked) begin
          ddr3_readdatavalid <= 1'b1;
          ddr3_readdata      <= dup_data;
          dup_pending        <= 1'b0;
          duped              <= duped + 1;
          emitted            = 1;
          if (do_trace) $display("[ddr3_model %0t] *** NONCONF dup: extra readdatavalid data=%h (duped=%0d) ***", $time, dup_data, duped+1);
        end

        // 3) otherwise, normal/non-conformant emission of a ready response.
        if (!emitted && !locked && ready0 >= 0 &&
            (late_after_reset == 0 || post_reset_cycles >= late_after_reset[31:0])) begin
          drain_count <= drain_count + 1;

          // DROP: every Nth ready response is retired with NO readdatavalid.
          if (mode_drop != 0 && ((drain_count + 1) % mode_drop == 0)) begin
            rsp_pending[ready0] <= 1'b0;     // consume it, but emit nothing
            dropped             <= dropped + 1;
            if (do_trace) $display("[ddr3_model %0t] *** NONCONF drop: response data=%h DROPPED (dropped=%0d) ***", $time, rsp_data[ready0], dropped+1);
          end
          else begin
            // REORDER: every Nth time, if a second slot is also ready, emit the
            // LATER one first (out-of-order response).
            chosen = ready0;
            if (mode_reorder != 0 && ready1 >= 0 && ((drain_count + 1) % mode_reorder == 0)) begin
              chosen    = ready1;
              reordered <= reordered + 1;
              if (do_trace) $display("[ddr3_model %0t] *** NONCONF reorder: emitting slot %0d before %0d (reordered=%0d) ***", $time, ready1, ready0, reordered+1);
            end

            ddr3_readdatavalid  <= 1'b1;
            ddr3_readdata       <= rsp_data[chosen];
            rsp_pending[chosen] <= 1'b0;
            rd_responded        <= rd_responded + 1;
            emitted             = 1;

            // DUP: arm a duplicate of this response for next cycle.
            if (mode_dup != 0 && ((drain_count + 1) % mode_dup == 0)) begin
              dup_pending <= 1'b1;
              dup_data    <= rsp_data[chosen];
              if (do_trace) $display("[ddr3_model %0t] *** NONCONF dup: arming duplicate of data=%h ***", $time, rsp_data[chosen]);
            end

            if (do_trace) $display("[ddr3_model %0t] RD respond data=%h (responded=%0d)", $time, rsp_data[chosen], rd_responded+1);
          end
        end
      end
    end
  end

  // ---------------------------------------------------------------------------
  // Final accounting on $finish / end-of-run (callable from the testbench).
  // ---------------------------------------------------------------------------
  task report_counts;
    begin
      $display("[ddr3_model] FINAL: rd_issued=%0d rd_responded=%0d wr_issued=%0d oob=%0d badwin=%0d @ %0t",
               rd_issued, rd_responded, wr_issued, oob_seen, badwin_seen, $time);
      $display("[ddr3_model] NONCONF FINAL: dropped=%0d duped=%0d reordered=%0d (rd_issued-rd_responded=%0d)",
               dropped, duped, reordered, rd_issued - rd_responded);
      $display("[ddr3_model] LOCK/PEAK FINAL: locked=%0d outstanding_peak=%0d lock_threshold=%0d",
               locked, outstanding_peak, lock_threshold);
    end
  endtask

endmodule
/* not truncated */
