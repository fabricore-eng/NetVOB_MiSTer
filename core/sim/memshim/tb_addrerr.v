/*
 * tb_addrerr.v — DIRECTED unit test of the mem_shim ADDR_ERR same-cycle
 * response collision (the [feed+ADDR_ERR] hazard).
 *
 * WHY a directed test (not the full decoder co-sim): the collision needs a real
 * ddr3_readdatavalid to land on the EXACT clk edge mem_shim processes an
 * ADDR_ERR CMD_READ in S_IDLE. In the full co-sim that alignment is governed by
 * the decoder's request cadence + the single-outstanding read discipline, and
 * empirically it almost never coincides (the oncollide injector produced 0
 * collisions). So the full co-sim CANNOT reliably exercise the hazard. This
 * directed bench drives the two stimuli onto the same cycle deterministically,
 * which is the only way to ADJUDICATE the RTL hazard and the fix.
 *
 * It drives the REAL core/MiSTer_MPEG2/rtl/mem_shim.sv (the unit under test),
 * with NO decoder and a trivial inline DDR3 responder so we control response
 * timing to the cycle.
 *
 * Stimulus, per scenario:
 *   1) Present a normal CMD_READ to a real address. mem_shim dispatches it to
 *      DDR3 (state S_WAIT), bridge accepts, mem_shim returns to S_IDLE.
 *   2) We schedule that read's ddr3_readdatavalid to return on the SAME cycle
 *      mem_shim is processing the NEXT request, which we make an ADDR_ERR
 *      CMD_READ. => line 109 (real) vs line 140/176 (synthetic) collide.
 *   3) We count how many mem_res_wr_en pulses come out and check their data.
 *
 * PASS criterion (CONTRACT): for the two issued reads (one real, one ADDR_ERR)
 * mem_shim must produce TWO response-FIFO writes, the real one carrying the
 * real DDR3 data (not 0). If the UNFIXED shim collapses them to ONE write (or
 * corrupts the real data to 0), that is the desync that strands the decoder.
 *
 * Run:  +scenario=collide   (force same-cycle collision; the hazard)
 *       +scenario=spaced    (control: ADDR_ERR read with NO coincident rdv)
 */

`include "timescale.v"

module tb_addrerr;

  localparam [21:0] ADDR_ERR  = 22'h1EFFFF;
  localparam [21:0] REAL_ADDR = 22'h001234;
  localparam [1:0]  CMD_READ  = 2'd2;
  localparam [63:0] REAL_DATA = 64'hDEAD_BEEF_CAFE_F00D;

  reg clk, rst_n;
  initial begin clk = 0; forever #5 clk = ~clk; end   // 100 MHz-ish
  initial begin rst_n = 0; repeat (4) @(posedge clk); rst_n = 1; end

  // ---- request side (we play the role of the framestore request FIFO) ----
  reg  [1:0]  req_cmd;
  reg  [21:0] req_addr;
  reg  [63:0] req_dta;
  reg         req_valid;
  wire        req_rd_en;       // mem_shim pulls when it consumes a word

  // ---- response side (we play the role of the framestore response FIFO) ----
  wire [63:0] res_dta;
  wire        res_wr_en;
  reg         res_almost_full;

  // ---- DDR3 bus ----
  wire [28:0] ddr3_addr;
  wire  [7:0] ddr3_burstcnt;
  wire        ddr3_read, ddr3_write;
  wire [63:0] ddr3_writedata;
  wire  [7:0] ddr3_byteenable;
  reg  [63:0] ddr3_readdata;
  reg         rdv_man;            // manually driven readdatavalid (control scen)
  reg         arm_collide;        // when 1, force rdv to track addrerr_branch
  wire        ddr3_readdatavalid; // = manual OR (armed & branch) -> exact coincidence
  reg         ddr3_waitrequest;

  mem_shim dut (
    .clk(clk), .rst_n(rst_n), .hard_rst_n(rst_n),
    .mem_req_rd_cmd(req_cmd), .mem_req_rd_addr(req_addr), .mem_req_rd_dta(req_dta),
    .mem_req_rd_en(req_rd_en), .mem_req_rd_valid(req_valid),
    .mem_res_wr_dta(res_dta), .mem_res_wr_en(res_wr_en), .mem_res_wr_almost_full(res_almost_full),
    .ddr3_addr(ddr3_addr), .ddr3_burstcnt(ddr3_burstcnt),
    .ddr3_read(ddr3_read), .ddr3_write(ddr3_write),
    .ddr3_writedata(ddr3_writedata), .ddr3_byteenable(ddr3_byteenable),
    .ddr3_readdata(ddr3_readdata), .ddr3_readdatavalid(ddr3_readdatavalid),
    .ddr3_waitrequest(ddr3_waitrequest),
    .debug_state(), .debug_saved_cmd(), .debug_sdram_busy(), .debug_sdram_ack(),
    .debug_rd_count(), .debug_wr_count(), .debug_rsp_count(), .debug_read_pend_cycles()
  );

  // -------------------------------------------------------------------------
  // Response capture + accounting.
  // -------------------------------------------------------------------------
  integer res_count;       // total mem_res_wr_en pulses
  integer res_zero;        // # of responses whose data was 64'd0
  integer res_real;        // # of responses carrying REAL_DATA
  always @(posedge clk) begin
    if (!rst_n) begin res_count <= 0; res_zero <= 0; res_real <= 0; end
    else if (res_wr_en) begin
      res_count <= res_count + 1;
      if (res_dta == 64'd0)      res_zero <= res_zero + 1;
      if (res_dta == REAL_DATA)  res_real <= res_real + 1;
      $display("[tb_addrerr %0t] mem_res_wr_en=1 dta=%h (resp #%0d)", $time, res_dta, res_count + 1);
    end
  end

  // -------------------------------------------------------------------------
  // Cycle trace + collision detector (probe mem_shim internals).
  // The collision = on one clk edge BOTH:
  //   (a) ddr3_readdatavalid==1 (line 109 wants mem_res_wr_en<=1, dta<=readdata)
  //   (b) mem_shim S_IDLE processes a CMD_READ to ADDR_ERR (line 140/176 set
  //       mem_res_wr_en<=1, dta<=0) -- detectable as state==0 + a valid ADDR_ERR
  //       read being consumed and !almost_full.
  // -------------------------------------------------------------------------
  integer dbg;
  initial dbg = $test$plusargs("dbg");
  wire dut_state = dut.state;
  wire dut_saved_valid = dut.saved_valid;
  // direct ADDR_ERR read branch active this cycle (S_IDLE, no skid, valid ADDR_ERR read)
  wire branch_direct = (dut.state==1'b0) && !dut.saved_valid &&
                       req_valid && (req_cmd==CMD_READ) && (req_addr==ADDR_ERR) && !res_almost_full;
  wire branch_skid   = (dut.state==1'b0) && dut.saved_valid &&
                       (dut.saved_cmd==CMD_READ) && (dut.saved_addr==ADDR_ERR) && !res_almost_full;
  wire addrerr_branch = branch_direct || branch_skid;

  // Drive the REAL read's response valid to EXACTLY coincide with the FIRST
  // cycle mem_shim takes the ADDR_ERR-read branch. While arm_collide is set, rdv
  // tracks addrerr_branch combinationally — but only for ONE pulse (rdv_fired),
  // because a real DDR3 read response is a single cycle. This guarantees the
  // collision lands on the same edge as the synthetic ADDR_ERR response while
  // keeping response accounting honest (one real read => one real response).
  reg rdv_fired;
  assign ddr3_readdatavalid = rdv_man || (arm_collide && addrerr_branch && !rdv_fired);
  reg addrerr_branch_fired;     // sticky: branch was taken at least once
  always @(posedge clk) begin
    if (!rst_n) begin rdv_fired <= 0; addrerr_branch_fired <= 0; end
    else begin
      if (arm_collide && addrerr_branch && !rdv_fired) rdv_fired <= 1; // 1-pulse rdv
      if (addrerr_branch) addrerr_branch_fired <= 1;
    end
  end

  integer collisions_seen;
  always @(posedge clk) begin
    if (!rst_n) collisions_seen <= 0;
    else begin
      if (dbg)
        $display("[trace %0t] state=%b saved=%b req_v=%b req_a=%h ddr3_rd=%b rdv=%b branch=%b(d%b/s%b) res_we=%b",
                 $time, dut.state, dut.saved_valid, req_valid, req_addr, ddr3_read,
                 ddr3_readdatavalid, addrerr_branch, branch_direct, branch_skid, res_wr_en);
      if (addrerr_branch && ddr3_readdatavalid) begin
        collisions_seen <= collisions_seen + 1;
        $display("[tb_addrerr %0t] *** SAME-CYCLE COLLISION: ADDR_ERR-read branch + ddr3_readdatavalid=1 (readdata=%h) ***", $time, ddr3_readdata);
      end
    end
  end

  // -------------------------------------------------------------------------
  // Scenario driver.
  // -------------------------------------------------------------------------
  reg [127:0] scen;
  integer ok;

  task drive_word;          // present one request word; consume EXACTLY once
    input [1:0]  c;
    input [21:0] a;
    begin
      // Present at a negedge so req_rd_en (sampled at the posedge) reflects this
      // word. The word is consumed on the posedge where (req_rd_en && req_valid);
      // deassert at the following negedge so the single-entry FIFO advances once.
      @(negedge clk);
      req_cmd = c; req_addr = a; req_dta = 0; req_valid = 1;
      @(posedge clk);
      while (!req_rd_en) @(posedge clk);   // wait until mem_shim pulls it (consumed THIS edge)
      @(negedge clk);
      req_valid = 0; req_addr = 0;
    end
  endtask

  initial begin
    scen = "collide";
    if ($value$plusargs("scenario=%s", scen));
    $display("[tb_addrerr] scenario=%0s", scen);

    req_cmd = 0; req_addr = 0; req_dta = 0; req_valid = 0;
    res_almost_full = 0;
    ddr3_readdata = 0; rdv_man = 0; arm_collide = 0; ddr3_waitrequest = 0;

    @(posedge rst_n);
    repeat (4) @(posedge clk);

    if (scen == "collide") begin
      // ----- 1) issue a REAL read and let it be accepted, but HOLD its
      //          response (rdv) so we can release it precisely on the cycle
      //          mem_shim processes a following ADDR_ERR read. -----
      // We keep ddr3_readdatavalid LOW (gating it ourselves) so the real read's
      // response is "in flight" exactly like single-outstanding HW latency.
      // Hold waitrequest HIGH initially so mem_shim parks in S_WAIT and we can
      // deassert req_valid (preventing a spurious skid capture of the real word)
      // before accepting.
      @(negedge clk);
      ddr3_waitrequest = 1;
      req_cmd = CMD_READ; req_addr = REAL_ADDR; req_dta = 0; req_valid = 1;
      // wait until mem_shim has captured the real read and asserted ddr3_read
      // (it is now in S_WAIT holding the command).
      @(posedge clk);
      while (!ddr3_read) @(posedge clk);
      // Drop req_valid so the S_WAIT skid-capture (if (mem_req_rd_valid &&
      // !saved_valid)) does NOT latch a stale word.
      @(negedge clk);
      req_valid = 0;
      @(posedge clk);              // a clean S_WAIT cycle, no skid captured
      // Now accept the real read; mem_shim -> S_IDLE clean (state=0, saved=0).
      @(negedge clk);
      ddr3_waitrequest = 0;
      @(posedge clk);              // accept edge -> next cycle S_IDLE
      while (!(dut.state==1'b0 && !dut.saved_valid)) @(posedge clk);
      // Present the ADDR_ERR read into clean S_IDLE, with the real read's data
      // STAGED but its response withheld. ARM the collision: ddr3_readdatavalid
      // now tracks addrerr_branch combinationally (one pulse), so the very cycle
      // mem_shim takes the synthetic ADDR_ERR branch, the REAL response is ALSO
      // asserted -> a guaranteed same-cycle collision (line 109/111 vs 140/176).
      @(negedge clk);
      req_cmd = CMD_READ; req_addr = ADDR_ERR; req_valid = 1;
      ddr3_readdata = REAL_DATA;
      arm_collide   = 1;
      // Hold the ADDR_ERR word until mem_shim consumes it. With the UNFIXED shim
      // the branch fires and consumes it the same cycle as the collision (real
      // data lost). With the FIXED shim the collision cycle HOLDS the FIFO read
      // (req_rd_en=0); the word is consumed one cycle later (rdv now clear) for
      // exactly one synthetic response. Consume on the FIRST cycle the branch
      // actually fires AND mem_shim pulls the word.
      // Wait until the ADDR_ERR-read branch has actually fired (sticky flag),
      // which is the cycle the synthetic response is generated and — because
      // arm_collide ties rdv to the branch — the cycle the real response also
      // fires (the collision). Then deassert the word (consumed exactly once).
      while (!addrerr_branch_fired) @(posedge clk);
      @(negedge clk);
      req_valid = 0; req_addr = 0; arm_collide = 0;

      // let any registered responses drain
      repeat (8) @(posedge clk);

      $display("[tb_addrerr] COLLIDE result: responses=%0d (real=%0d zero=%0d)", res_count, res_real, res_zero);
      // CONTRACT: 2 reads issued (real + ADDR_ERR) => 2 responses; the real one
      // must carry REAL_DATA, the synthetic one is 0.
      ok = (res_count == 2) && (res_real == 1) && (res_zero == 1);
      if (ok) $display("[tb_addrerr] PASS: both responses present; real data preserved.");
      else begin
        $display("[tb_addrerr] *** FAIL: response collapse/corruption ***");
        $display("[tb_addrerr] expected responses=2 real=1 zero=1; got responses=%0d real=%0d zero=%0d", res_count, res_real, res_zero);
        if (res_count < 2) $display("[tb_addrerr]   -> a logical response was LOST (FIFO write collapsed): permanent tag/response desync -> framestore garbage -> black.");
        if (res_real == 0) $display("[tb_addrerr]   -> the REAL DDR3 data was OVERWRITTEN with synthetic 0: corrupt reference -> garbage/black.");
      end
    end
    else begin
      // ----- control: ADDR_ERR read with NO coincident readdatavalid -----
      drive_word(CMD_READ, ADDR_ERR);
      repeat (8) @(posedge clk);
      $display("[tb_addrerr] SPACED result: responses=%0d (real=%0d zero=%0d)", res_count, res_real, res_zero);
      ok = (res_count == 1) && (res_zero == 1);
      if (ok) $display("[tb_addrerr] PASS: lone ADDR_ERR read yields exactly one synthetic 0 response.");
      else    $display("[tb_addrerr] *** FAIL: spaced ADDR_ERR response accounting wrong ***");
    end

    $finish;
  end

  // safety timeout
  initial begin #20000; $display("[tb_addrerr] TIMEOUT"); $finish; end

endmodule
/* not truncated */
