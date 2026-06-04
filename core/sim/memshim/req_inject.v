/*
 * req_inject.v — request-port ADDR_ERR fault injector for the memshim co-sim.
 *
 * Sits transparently between the decoder's memory-REQUEST port
 * (mpeg2video.mem_req_rd_*) and mem_shim's request inputs, on the mem_clk
 * domain (the same domain mem_shim reads the request FIFO on). It can rewrite a
 * decoder read request's address to ADDR_ERR (22'h1EFFFF) so that mem_shim
 * takes its synthetic-ADDR_ERR-read branch — WITHOUT changing the decoder or
 * mem_shim RTL. This is how we exercise the ADDR_ERR same-cycle collision
 * hazard (mem_shim.sv lines 109/111 vs 139-140/175-176) on a clip (greyramp)
 * that never naturally emits ADDR_ERR.
 *
 * The decoder's tag-checker (framestore_response.v:248, compiled in via
 * -D__IVERILOG__ => CHECK) will $stop on any response/tag desync, so if forcing
 * ADDR_ERR collisions corrupts the response stream we get a hard, unambiguous
 * trap — the decisive repro signal.
 *
 * CRITICAL FIDELITY NOTE: we only rewrite the *address* presented to mem_shim.
 * We do NOT alter mem_req_rd_valid/cmd/dta or the rd_en backpressure, and we
 * pass mem_req_rd_en straight back to the decoder. So from the decoder's tag
 * FIFO's perspective the request is consumed exactly once (its tag is enqueued
 * for a real Y/C location), but mem_shim believes it was ADDR_ERR and returns a
 * synthetic 0 instead of the real data. That is precisely the HW failure mode
 * the [feed+ADDR_ERR] analysis describes when a real ADDR_ERR read coincides
 * with a real readdatavalid: the tag FIFO advances but the data FIFO either
 * gets the wrong (synthetic) value or the two collapse. It lets the tag-checker
 * adjudicate whether mem_shim's ADDR_ERR handling can desync the pipeline.
 *
 * Modes (plusargs; 0 = disabled / pass-through):
 *   +inj_addrerr_period=N  rewrite a decoder READ to ADDR_ERR once every N reads
 *                          that mem_shim accepts off the request port.
 *   +inj_addrerr_oncollide rewrite a decoder READ to ADDR_ERR ONLY on cycles
 *                          where ddr3_readdatavalid is HIGH — i.e. force the
 *                          exact same-cycle real-response/synthetic-ADDR_ERR
 *                          collision (the precise hazard). Bounded by
 *                          +inj_addrerr_max so the run still terminates.
 *   +inj_addrerr_max=M     cap total injections (default 64). 0 = unlimited.
 *   +inj_trace             per-injection $display.
 */

`include "timescale.v"

module req_inject (
    input             clk,            // mem_clk
    input             rst_n,

    // From decoder request port
    input       [1:0] in_cmd,
    input      [21:0] in_addr,
    input      [63:0] in_dta,
    input             in_valid,

    // To mem_shim request inputs (addr possibly rewritten to ADDR_ERR)
    output      [1:0] out_cmd,
    output reg [21:0] out_addr,
    output     [63:0] out_dta,
    output            out_valid,

    // mem_shim's read-enable (passes straight back to decoder)
    input             shim_rd_en,
    output            dec_rd_en,

    // Observe the DDR3 response strobe to time the collision
    input             ddr3_readdatavalid,

    output reg [31:0] inj_count
);

  localparam [21:0] ADDR_ERR  = 22'h1EFFFF;
  localparam [1:0]  CMD_READ  = 2'd2;

  integer period;
  integer on_collide;
  integer inj_max;
  integer do_trace;

  initial begin
    period     = 0;
    on_collide = 0;
    inj_max    = 64;
    do_trace   = 0;
    if ($value$plusargs("inj_addrerr_period=%d", period));
    if ($test$plusargs("inj_addrerr_oncollide")) on_collide = 1;
    if ($value$plusargs("inj_addrerr_max=%d", inj_max));
    if ($test$plusargs("inj_trace")) do_trace = 1;
    $display("[req_inject] addrerr_period=%0d oncollide=%0d max=%0d", period, on_collide, inj_max);
  end

  // Pass-throughs (only the address can change)
  assign out_cmd   = in_cmd;
  assign out_dta   = in_dta;
  assign out_valid = in_valid;
  assign dec_rd_en = shim_rd_en;

  // Count reads that mem_shim actually consumes off the request port: a read is
  // consumed on the cycle mem_shim asserts rd_en while a valid read is present.
  reg [31:0] read_seen;
  wire read_consumed = in_valid && (in_cmd == CMD_READ) && shim_rd_en;

  // Decide injection combinationally so out_addr is rewritten the SAME cycle the
  // request is presented to mem_shim (and, for oncollide, the same cycle as the
  // real readdatavalid). inj_count/read_seen are registered for cadence + cap.
  reg do_inject;
  always @* begin
    do_inject = 1'b0;
    if (rst_n && in_valid && (in_cmd == CMD_READ) && (in_addr != ADDR_ERR) &&
        ((inj_max == 0) || (inj_count < inj_max[31:0]))) begin
      if (on_collide) begin
        if (ddr3_readdatavalid) do_inject = 1'b1;       // exact same-cycle collision
      end else if (period != 0) begin
        if (((read_seen + 1) % period) == 0 && read_consumed) do_inject = 1'b1;
      end
    end
    out_addr = do_inject ? ADDR_ERR : in_addr;
  end

  always @(posedge clk) begin
    if (!rst_n) begin
      read_seen <= 0;
      inj_count <= 0;
    end else begin
      if (read_consumed) read_seen <= read_seen + 1;
      // Count an injection when the rewritten ADDR_ERR read is actually consumed
      // (period mode) or whenever we assert it under collision mode (it is held
      // until consumed by mem_shim's not-almost-full path).
      if (do_inject && (on_collide ? 1'b1 : read_consumed)) begin
        inj_count <= inj_count + 1;
        if (do_trace)
          $display("[req_inject %0t] INJECT ADDR_ERR over addr=%h (inj=%0d) ddr3_rdv=%b",
                   $time, in_addr, inj_count + 1, ddr3_readdatavalid);
      end
    end
  end

endmodule
/* not truncated */
