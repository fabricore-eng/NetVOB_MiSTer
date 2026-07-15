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
                              // NOTE: this command-gap theory was REFUTED by SignalTap 2026-06-25
                              // (responses drain fine during normal gaps). Kept for regression only;
                              // the TRUE wedge is drop_then_write_wedge below.
  integer drop_then_write_wedge; // TRUE ROOT CAUSE (SignalTap 2026-06-25): a read response is
                              // marginally DROPPED, then a WRITE issued while that read is still
                              // outstanding sticks the bridge BUSY forever. This is the wedge the
                              // write-gate fix targets. Requires +ddr_drop=N to actually drop. 0=off.
  integer wedge_window;       // cycles the lost-read dependency persists at the bridge after a drop.
                              // The write-gate fix works IFF it holds the write PAST this window
                              // (i.e. window < the shim's resp_timeout=16383). Default 4096 (well
                              // under resp_timeout, well over the unfixed write-after-drop latency).

  // LFSR for pseudo-random jitter / waitrequest phase
  reg [31:0] lfsr;

  // HW-divergence knobs (2026-07-01, slice-2 stall oracle). All default OFF = the
  // model stays byte-identical to the age-gate baseline (proven via the settled-frame
  // fingerprints 20d53898/01d88a70).
  integer seed_arg;           // +ddr_seed=S            LFSR seed (jitter/corruption runs become seed-variable)
  integer wr_commit_delay;    // +ddr_wr_commit_delay=N posted-write RAW hazard: a write is ACCEPTED
                              //                        immediately but commits to mem[] only N cycles
                              //                        later; a read accepted in that window samples the
                              //                        STALE value (the f2sdram bridge-level read-after-
                              //                        posted-write hazard the coherent model can't show).
  integer corrupt_rd;         // +ddr_corrupt_rd=N      flip one seeded bit in every Nth read response
  integer corrupt_wr;         // +ddr_corrupt_wr=N      flip one seeded bit in every Nth committed write
                              //                        (models a setup/hold-marginal write datapath)
  integer corrupt_lo;         // +ddr_corrupt_lo=H      corruption window low word addr (default 0)
  integer corrupt_hi;         // +ddr_corrupt_hi=H      corruption window high word addr (default END_OF_MEM)
  integer refresh_period;     // +ddr_refresh_period=M  every M cycles ...
  integer refresh_hold;       // +ddr_refresh_hold=H    ... hold waitrequest high for H cycles (refresh/
                              //                        arbitration outage bursts the master never sees in
                              //                        the fixed-latency model)
  integer tail_drop_period;   // +ddr_tail_drop_period=N  PARTIAL-BURST LOSS (2026-07-15 review): on every
                              //                        Nth burst read (len>1) schedule only beats 0..len-2
                              //                        and silently drop the LAST beat — models the f2sdram
                              //                        wedging mid-burst (beat 0 delivered, tail lost). This
                              //                        is the exact event the mem_shim resp_timeout recovery
                              //                        exists for and that whole-transaction +ddr_drop never
                              //                        produced; it exercises the burst-recovery paths.
  integer tail_drop_max;      // +ddr_tail_drop_max=M   cap total tail-drops (default 8) so recovery stalls
                              //                        (16383 cyc each) don't starve the frame budget.
  integer burst_rd_count;     // accepted burst reads (len>1), for the period counter
  integer tail_dropped;
  integer outage_at;          // +ddr_outage_at=T   RECOVERABLE FULL BEAT OUTAGE (2026-07-15): from
                              //                    mem_clk cycle T, HOLD all readdatavalid emission
  integer outage_len;         // +ddr_outage_len=W  for W cycles, then resume. Beats are DEFERRED
                              //                    (not dropped): countdowns keep ticking, emission
                              //                    is gated, they flush in seq order afterwards. This
                              //                    is the ONLY way to make mem_shim's resp_timeout
                              //                    fire (16383-cycle beat silence with txns
                              //                    outstanding) — it exercises the burst-recovery
                              //                    retire path (review 2026-07-15 Bugs 1 & 2). Commands
                              //                    are still accepted so the shim keeps its state.

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
    drop_then_write_wedge = 0;
    wedge_window = 4096;
    lfsr         = 32'hACE1_2345;
    seed_arg     = 0;
    wr_commit_delay = 0;
    corrupt_rd   = 0;
    corrupt_wr   = 0;
    corrupt_lo   = 0;
    corrupt_hi   = END_OF_MEM[21:0];
    refresh_period = 0;
    refresh_hold   = 0;
    tail_drop_period = 0;
    tail_drop_max    = 8;
    burst_rd_count   = 0;
    tail_dropped     = 0;
    outage_at        = 0;
    outage_len       = 0;
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
    if ($value$plusargs("ddr_drop_then_write_wedge=%d", drop_then_write_wedge));
    if ($value$plusargs("ddr_wedge_window=%d", wedge_window));
    if ($value$plusargs("ddr_seed=%d", seed_arg) && seed_arg != 0) lfsr = seed_arg[31:0];
    if ($value$plusargs("ddr_wr_commit_delay=%d", wr_commit_delay));
    if ($value$plusargs("ddr_corrupt_rd=%d", corrupt_rd));
    if ($value$plusargs("ddr_corrupt_wr=%d", corrupt_wr));
    if ($value$plusargs("ddr_corrupt_lo=%h", corrupt_lo));
    if ($value$plusargs("ddr_corrupt_hi=%h", corrupt_hi));
    if ($value$plusargs("ddr_refresh_period=%d", refresh_period));
    if ($value$plusargs("ddr_refresh_hold=%d", refresh_hold));
    if ($value$plusargs("ddr_tail_drop_period=%d", tail_drop_period));
    if ($value$plusargs("ddr_tail_drop_max=%d", tail_drop_max));
    if ($value$plusargs("ddr_outage_at=%d", outage_at));
    if ($value$plusargs("ddr_outage_len=%d", outage_len));
    if (zero_latency) begin rd_latency = 0; wait_period = 0; rd_jitter = 0; end
    $display("[ddr3_model] rd_latency=%0d wait_period=%0d rd_jitter=%0d zero_latency=%0d",
             rd_latency, wait_period, rd_jitter, zero_latency);
    $display("[ddr3_model] NONCONFORMANT modes: reorder=%0d drop=%0d dup=%0d late_after_reset=%0d lock_threshold=%0d gap_wedge=%0d",
             mode_reorder, mode_drop, mode_dup, late_after_reset, lock_threshold, gap_wedge);
    $display("[ddr3_model] drop_then_write_wedge=%0d wedge_window=%0d",
             drop_then_write_wedge, wedge_window);
    $display("[ddr3_model] HW-DIVERGENCE knobs: seed=%08h wr_commit_delay=%0d corrupt_rd=%0d corrupt_wr=%0d corrupt_win=[%h..%h] refresh=%0d/%0d",
             lfsr, wr_commit_delay, corrupt_rd, corrupt_wr, corrupt_lo, corrupt_hi, refresh_period, refresh_hold);
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

  // Refresh/arbitration outage: every refresh_period cycles, hold waitrequest
  // high for refresh_hold cycles (commands stall; already-scheduled responses
  // keep draining, like a controller finishing in-flight reads around a refresh).
  reg [31:0] refresh_ctr;
  reg        refresh_busy;
  always @(posedge clk)
    if (rst) begin refresh_ctr <= 0; refresh_busy <= 0; end
    else if (refresh_period <= 0) begin refresh_ctr <= 0; refresh_busy <= 0; end
    else begin
      refresh_ctr <= refresh_ctr + 1;
      if (!refresh_busy && refresh_ctr >= refresh_period) begin
        refresh_busy <= 1; refresh_ctr <= 0;
      end else if (refresh_busy && refresh_ctr >= refresh_hold) begin
        refresh_busy <= 0; refresh_ctr <= 0;
      end
    end

  assign ddr3_waitrequest = locked ? 1'b1 :
                            refresh_busy ? 1'b1 :
                            (wait_period <= 1) ? 1'b0 : (wait_phase != 16'd0);

  wire cmd_accept_rd = ddr3_read  && !ddr3_waitrequest;
  wire cmd_accept_wr = ddr3_write && !ddr3_waitrequest;

  // ---------------------------------------------------------------------------
  // Read-response pipeline. A simple shift-register of pending responses keyed
  // by countdown. Single-outstanding is the common case (mem_shim serializes),
  // but we support a small depth so the model is not artificially in-lockstep.
  // Each accepted read schedules a response 'lat' cycles out with its data.
  // ---------------------------------------------------------------------------
  localparam PIPE = 64;            // max outstanding response BEATS tracked
  reg        rsp_pending [0:PIPE-1];
  reg [31:0] rsp_countdown[0:PIPE-1];
  reg [63:0] rsp_data     [0:PIPE-1];
  // BURST support (2026-07-14): one accepted read with ddr3_burstcnt=N schedules N
  // beats — first at cur_lat, the rest back-to-back (+1 cycle each), data sampled
  // mem[addr+k] at accept. Beats carry a global sequence number and the drain emits
  // strictly in sequence order (the f2sdram returns responses strictly in order;
  // the old lowest-free-slot-index scan could interleave two in-flight bursts).
  // rsp_last marks the final beat of its transaction (drives rd_responded).
  reg [31:0] rsp_seq      [0:PIPE-1];
  reg        rsp_last     [0:PIPE-1];
  reg [31:0] seq_next;             // next sequence number to assign
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

  // drop-then-write-block wedge bookkeeping (the TRUE root cause)
  reg        rd_dropped_outstanding;  // a genuinely-dropped read is still "in flight" at the bridge
  reg [31:0] dropped_age;             // cycles since that drop (clears the flag at wedge_window)

  // Posted-write commit queue (+ddr_wr_commit_delay): FIFO order = accept order,
  // so same-address write ordering is preserved; only READ-vs-posted-WRITE can skew.
  localparam WQ = 256;
  reg        wq_pending [0:WQ-1];
  reg [21:0] wq_addr    [0:WQ-1];
  reg [63:0] wq_data    [0:WQ-1];
  reg [31:0] wq_age     [0:WQ-1];
  integer    wq_head, wq_tail;
  reg [31:0] raw_stale_reads;   // reads accepted while a same-address write was still posted
  reg [31:0] wq_overflows;
  integer    enq_slot;          // slot enqueued THIS cycle (-1 = none): guards the tick loop
                                // from clearing a just-enqueued entry when the queue is full
  // corruption bookkeeping
  reg [31:0] corrupt_rd_count, corrupt_wr_count;   // in-window op counters
  reg [31:0] corrupted_rd, corrupted_wr;           // corruptions applied
  integer    j;

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
      rd_dropped_outstanding <= 1'b0;
      dropped_age        <= 0;
      locked             <= 1'b0;
      outstanding_peak   <= 0;
      cmd_gap            <= 0;
      for (k = 0; k < PIPE; k = k + 1) begin
        rsp_pending[k]   <= 1'b0;
        rsp_countdown[k] <= 0;
        rsp_data[k]      <= 64'd0;
        rsp_seq[k]       <= 0;
        rsp_last[k]      <= 1'b1;
      end
      seq_next <= 0;
      wq_head <= 0; wq_tail <= 0;
      raw_stale_reads <= 0; wq_overflows <= 0;
      corrupt_rd_count <= 0; corrupt_wr_count <= 0;
      corrupted_rd <= 0; corrupted_wr <= 0;
      for (k = 0; k < WQ; k = k + 1) begin
        wq_pending[k] <= 1'b0; wq_addr[k] <= 22'd0; wq_data[k] <= 64'd0; wq_age[k] <= 0;
      end
    end
    else begin
      ddr3_readdatavalid <= 1'b0;   // default: no response this cycle
      enq_slot = -1;                // no posted-write enqueue yet this cycle

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

      // ---- TRUE ROOT CAUSE: drop-then-write-block wedge (SignalTap 2026-06-25) ----
      // A genuinely-dropped read leaves a lost-read dependency at the bridge. If a WRITE
      // is accepted while that dependency persists, the bridge sticks BUSY forever. The
      // dependency ages out after wedge_window cycles (models the bridge/shim resolving
      // the lost read); the write-gate fix works by holding the write past that window.
      if (rd_dropped_outstanding) begin
        if (dropped_age >= wedge_window[31:0])
          rd_dropped_outstanding <= 1'b0;   // lost-read dependency aged out / resolved
        else
          dropped_age <= dropped_age + 1;
      end

      // ---- accept a WRITE ----
      if (cmd_accept_wr) begin : wr_accept
        reg [63:0] wdata_eff;
        if (!locked && drop_then_write_wedge != 0 && rd_dropped_outstanding) begin
          locked <= 1'b1;
          $display("[ddr3_model %0t] *** DROP-THEN-WRITE-WEDGE: write accepted while a dropped read is still outstanding (age=%0d < window=%0d) -- f2sdram locked (BUSY stuck, responses stop) ***",
                   $time, dropped_age, wedge_window);
        end
        wr_issued <= wr_issued + 1;
        if (window != 7'b0011000) badwin_seen <= 1'b1;
        if (word_addr > END_OF_MEM[21:0]) begin
          oob_seen <= 1'b1;
          if (do_trace) $display("[ddr3_model %0t] WR OOB word_addr=%h (> END_OF_MEM)", $time, word_addr);
        end else begin
          // optional marginal-write-datapath corruption (+ddr_corrupt_wr, windowed)
          wdata_eff = ddr3_writedata;
          if (corrupt_wr != 0 && word_addr >= corrupt_lo[21:0] && word_addr <= corrupt_hi[21:0]) begin
            corrupt_wr_count <= corrupt_wr_count + 1;
            if (((corrupt_wr_count + 1) % corrupt_wr) == 0) begin
              wdata_eff = ddr3_writedata ^ (64'h1 << lfsr[5:0]);
              corrupted_wr <= corrupted_wr + 1;
              $display("[ddr3_model %0t] *** CORRUPT-WR: word=%h bit=%0d (corrupted_wr=%0d) ***",
                       $time, word_addr, lfsr[5:0], corrupted_wr+1);
            end
          end
          if (wr_commit_delay <= 0) begin
            mem[word_addr] <= wdata_eff;      // immediate commit = baseline behavior
          end else begin
            // posted write: accepted now, commits wr_commit_delay cycles later.
            if (wq_pending[wq_tail]) begin
              // queue full: force-commit the head NOW (never drop a write), loudly.
              wq_overflows <= wq_overflows + 1;
              mem[wq_addr[wq_head]] <= wq_data[wq_head];
              wq_head <= (wq_head + 1) % WQ;
              $display("[ddr3_model %0t] *** WQ OVERFLOW: force-committed head (overflows=%0d) ***",
                       $time, wq_overflows+1);
            end
            wq_pending[wq_tail] <= 1'b1;
            wq_addr[wq_tail]    <= word_addr;
            wq_data[wq_tail]    <= wdata_eff;
            wq_age[wq_tail]     <= 0;
            enq_slot            = wq_tail;
            wq_tail             <= (wq_tail + 1) % WQ;
          end
        end
        if (do_trace) $display("[ddr3_model %0t] WR accept addr=%h word=%h dta=%h", $time, ddr3_addr, word_addr, ddr3_writedata);
      end

      // ---- posted-write queue: age everything, commit expired entries in FIFO order ----
      if (wr_commit_delay > 0) begin : wq_tick
        integer idx;
        integer stop;
        for (k = 0; k < WQ; k = k + 1)
          if (wq_pending[k]) wq_age[k] <= wq_age[k] + 1;
        stop = 0;
        for (j = 0; j < WQ; j = j + 1) begin
          idx = (wq_head + j) % WQ;
          if (!stop) begin
            if (idx == enq_slot) stop = 1;   // never clear a just-enqueued entry (full-queue corner)
            else if (wq_pending[idx] && wq_age[idx] >= wr_commit_delay) begin
              mem[wq_addr[idx]] <= wq_data[idx];   // ascending j = accept order; same-addr order preserved
              wq_pending[idx]   <= 1'b0;
              wq_head           <= (idx + 1) % WQ;
            end else stop = 1;
          end
        end
      end

      // ---- accept a READ: schedule a response ----
      if (cmd_accept_rd) begin
        rd_issued <= rd_issued + 1;
        if (window != 7'b0011000) badwin_seen <= 1'b1;
        // RAW-STALE detection: a read accepted while a posted write to the SAME
        // address is still uncommitted returns the stale value (the f2sdram
        // read-after-posted-write hazard). Counted + printed for diagnosis.
        if (wr_commit_delay > 0) begin : raw_check
          integer hit;
          integer rb;
          hit = 0;
          for (j = 0; j < WQ; j = j + 1)
            for (rb = 0; rb < ((ddr3_burstcnt == 0) ? 1 : ddr3_burstcnt); rb = rb + 1)
              if (wq_pending[j] && wq_addr[j] == word_addr + rb[21:0]) hit = 1;
          if (hit) begin
            raw_stale_reads <= raw_stale_reads + 1;
            $display("[ddr3_model %0t] *** RAW-STALE: read word=%h served STALE (posted write in flight; raw_stale=%0d) ***",
                     $time, word_addr, raw_stale_reads+1);
          end
        end
        // latency = base + optional jitter (per TRANSACTION; beats stay contiguous)
        cur_lat = rd_latency;
        if (rd_jitter > 0) cur_lat = rd_latency + (lfsr % (rd_jitter+1));
        if (cur_lat < 1) cur_lat = 1;   // at least 1 cycle: response is registered
        // schedule burstcnt beats: beat b ready at cur_lat+b, data mem[addr+b]
        begin : alloc
          integer free_slot;
          integer b;
          integer nbeats;
          integer sched_beats;           // beats actually scheduled (< nbeats when tail-dropping)
          reg [PIPE-1:0] taken;          // slots claimed this cycle (rsp_pending is NBA-stale)
          reg [21:0] beat_addr;
          nbeats = (ddr3_burstcnt == 0) ? 1 : ddr3_burstcnt;  // burstcnt=0 is illegal Avalon; treat as 1
          sched_beats = nbeats;
          // PARTIAL-BURST LOSS: on every tail_drop_period-th burst read, drop the
          // last beat (schedule nbeats-1). seq_next still advances by nbeats so the
          // in-order drain never delivers the missing beat — the transaction wedges
          // until mem_shim's resp_timeout retires it. (Counted, capped.)
          if (tail_drop_period != 0 && nbeats > 1 && tail_dropped < tail_drop_max) begin
            burst_rd_count = burst_rd_count + 1;
            if ((burst_rd_count % tail_drop_period) == 0) begin
              sched_beats  = nbeats - 1;
              tail_dropped <= tail_dropped + 1;
              $display("[ddr3_model %0t] *** TAIL-DROP: burst addr=%h len=%0d — dropping last beat (tail_dropped=%0d) ***",
                       $time, word_addr, nbeats, tail_dropped + 1);
            end
          end
          taken = {PIPE{1'b0}};
          for (b = 0; b < sched_beats; b = b + 1) begin
            beat_addr = word_addr + b[21:0];
            free_slot = -1;
            for (k = 0; k < PIPE; k = k + 1)
              if (!rsp_pending[k] && !taken[k] && free_slot < 0) free_slot = k;
            if (free_slot < 0) begin
              $display("[ddr3_model %0t] *** ERROR: response pipe overflow (>%0d outstanding beats) ***", $time, PIPE);
            end else begin
              taken[free_slot]         = 1'b1;
              rsp_pending[free_slot]   <= 1'b1;
              rsp_countdown[free_slot] <= cur_lat[31:0] + b;
              rsp_seq[free_slot]       <= seq_next + b;
              rsp_last[free_slot]      <= (b == nbeats - 1);
              if (beat_addr > END_OF_MEM[21:0]) begin
                oob_seen <= 1'b1;
                rsp_data[free_slot] <= 64'd0;
                if (do_trace) $display("[ddr3_model %0t] RD OOB word_addr=%h -> 0", $time, beat_addr);
              end else begin
                // optional read-response bit corruption (+ddr_corrupt_rd, windowed)
                if (corrupt_rd != 0 && beat_addr >= corrupt_lo[21:0] && beat_addr <= corrupt_hi[21:0]) begin
                  corrupt_rd_count <= corrupt_rd_count + 1;
                  if (((corrupt_rd_count + 1) % corrupt_rd) == 0) begin
                    rsp_data[free_slot] <= mem[beat_addr] ^ (64'h1 << lfsr[5:0]);
                    corrupted_rd <= corrupted_rd + 1;
                    $display("[ddr3_model %0t] *** CORRUPT-RD: word=%h bit=%0d (corrupted_rd=%0d) ***",
                             $time, beat_addr, lfsr[5:0], corrupted_rd+1);
                  end else
                    rsp_data[free_slot] <= mem[beat_addr];
                end else
                  rsp_data[free_slot] <= mem[beat_addr];
              end
            end
          end
          seq_next <= seq_next + nbeats;
          if (do_trace) $display("[ddr3_model %0t] RD accept addr=%h word=%h burst=%0d lat=%0d (issued=%0d)", $time, ddr3_addr, word_addr, nbeats, cur_lat, rd_issued+1);
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
        reg     outage_now;        // this cycle is inside the +ddr_outage window
        integer ready0;            // ready slot with the SMALLEST sequence number (in-order)
        integer ready1;            // ready slot with the second-smallest seq (for reorder)
        integer chosen;
        emitted = 0;
        ready0  = -1;
        ready1  = -1;

        // 1) tick down all countdowns; pick the two lowest-seq ready slots.
        //    (Sequence order == accept+beat order == the f2sdram's strict
        //    response order. The old lowest-INDEX scan could interleave beats
        //    of two in-flight bursts once slots recycle.)
        for (k = 0; k < PIPE; k = k + 1) begin
          if (rsp_pending[k]) begin
            if (rsp_countdown[k] <= 1) begin
              if (ready0 < 0 || rsp_seq[k] < rsp_seq[ready0]) begin
                ready1 = ready0;
                ready0 = k;
              end else if (ready1 < 0 || rsp_seq[k] < rsp_seq[ready1]) begin
                ready1 = k;
              end
            end else begin
              rsp_countdown[k] <= rsp_countdown[k] - 1;
            end
          end
        end

        // 1b) RECOVERABLE FULL OUTAGE: while in the outage window, hold all
        //     emission (beats stay pending, countdowns already ticked). Countdowns
        //     saturate at 1 (the ready test is <=1) so no beat is lost; they flush
        //     in seq order once the window ends. This starves mem_shim of beats ->
        //     resp_timer saturates -> resp_timeout fires (the recovery path).
        outage_now = (outage_len != 0) && (post_reset_cycles >= outage_at[31:0])
                     && (post_reset_cycles < (outage_at[31:0] + outage_len[31:0]));
        if (outage_now) begin
          if (post_reset_cycles == outage_at[31:0])
            $display("[ddr3_model %0t] *** OUTAGE START: holding all beats for %0d cycles (post_reset=%0d) ***",
                     $time, outage_len, post_reset_cycles);
        end else if ((outage_len != 0) && (post_reset_cycles == (outage_at[31:0] + outage_len[31:0])))
          $display("[ddr3_model %0t] *** OUTAGE END: resuming beat delivery (post_reset=%0d) ***", $time, post_reset_cycles);

        // 2) emit a pending DUPLICATE first (extra response, contract violation).
        if (dup_pending && !locked && !outage_now) begin
          ddr3_readdatavalid <= 1'b1;
          ddr3_readdata      <= dup_data;
          dup_pending        <= 1'b0;
          duped              <= duped + 1;
          emitted            = 1;
          if (do_trace) $display("[ddr3_model %0t] *** NONCONF dup: extra readdatavalid data=%h (duped=%0d) ***", $time, dup_data, duped+1);
        end

        // 3) otherwise, normal/non-conformant emission of a ready response.
        if (!emitted && !locked && !outage_now && ready0 >= 0 &&
            (late_after_reset == 0 || post_reset_cycles >= late_after_reset[31:0])) begin
          drain_count <= drain_count + 1;

          // DROP: every Nth ready response is retired with NO readdatavalid.
          if (mode_drop != 0 && ((drain_count + 1) % mode_drop == 0)) begin
            rsp_pending[ready0] <= 1'b0;     // consume it, but emit nothing
            dropped             <= dropped + 1;
            if (drop_then_write_wedge != 0) begin
              rd_dropped_outstanding <= 1'b1;   // arm the write-behind-lost-read wedge
              dropped_age            <= 0;
            end
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
            // rd_responded counts completed TRANSACTIONS (last beat emitted), so
            // outstanding_now = rd_issued - rd_responded stays txn-granular under bursts.
            if (rsp_last[chosen]) rd_responded <= rd_responded + 1;
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
      $display("[ddr3_model] WEDGE-MODEL FINAL: drop_then_write_wedge=%0d wedge_window=%0d rd_dropped_outstanding=%0d",
               drop_then_write_wedge, wedge_window, rd_dropped_outstanding);
      $display("[ddr3_model] HW-DIVERGENCE FINAL: raw_stale_reads=%0d wq_overflows=%0d corrupted_rd=%0d corrupted_wr=%0d",
               raw_stale_reads, wq_overflows, corrupted_rd, corrupted_wr);
    end
  endtask

endmodule
/* not truncated */
