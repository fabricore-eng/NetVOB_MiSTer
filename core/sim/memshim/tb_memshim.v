/*
 * tb_memshim.v — co-sim testbench: mpeg2fpga decoder + REAL mem_shim + DDR3 model.
 *
 * core/sim/memshim/ harness. Mirrors the proven core/sim bench (testbench.v)
 * but replaces the bench mem_ctl.v with the actual core/MiSTer_MPEG2 mem_shim.sv
 * and a realistic Avalon-MM/f2sdram DDR3 slave (ddr3_model.v). The decode path,
 * the elementary-stream feed, and the tv_out PPM dump are kept byte-for-byte
 * identical to the proven harness so that the ONLY changed variable is the
 * memory controller behind the framestore's CDC FIFOs.
 *
 * CLOCKING matches the REAL design (emu.sv), NOT the bench:
 *   clk     = 27 MHz   (mpeg2video.clk      = clk_sys)
 *   mem_clk = 108 MHz  (mpeg2video.mem_clk  = clk_mem; mem_shim.clk; ddr3 clk)
 *   dot_clk = 27 MHz   (mpeg2video.dot_clk  = clk_sys = CLK_VIDEO)
 * (The bench used 75/125/27. Using the real 27/108/27 reproduces the HW CDC
 *  ratio: request FIFO wr@27 rd@108, response FIFO wr@108 rd@27.)
 *
 * RESET: pristine mpeg2video.rst is ACTIVE-LOW ("active low reset, internally
 * synchronized"); the bench drives it active-HIGH-after-8-cycles via rst_ff[7].
 * We follow the bench convention (rst high = released) and feed mem_shim its
 * active-low rst_n as ~that-not… see the rst/rst_n wiring below.
 *
 * STALL DETECTION: the upstream testbench free-runs forever with no $finish.
 * We add a progress watchdog: if no framestore write (CMD_WRITE to a FRAME_*_Y
 * base) and no new ddr3 response occurs for WATCHDOG_NS, we dump a full
 * localization report (mem_shim FSM state, saved-skid, ddr3 counters, waitrequest)
 * and $finish. We also honor the decoder's own tag-sync $stop in
 * framestore_response.v:248 (fires on response/tag desync).
 */

`include "timescale.v"

`define MAX_STREAM_LENGTH 4194304

module tb_memshim();

  // ---- clocks (real design ratios) ----
  // periods in ns: 27MHz -> 37.04ns, 108MHz -> 9.26ns
  localparam real CLK_PERIOD     = 37.04;   // 27 MHz  (clk, dot_clk)
  localparam real MEMCLK_PERIOD  = 9.26;    // 108 MHz (mem_clk, mem_shim, ddr3)

  reg clk;      // 27 MHz system clock
  reg mem_clk;  // 108 MHz memory clock
  reg dot_clk;  // 27 MHz dot clock

  initial begin clk = 0;     #(CLK_PERIOD/2);    forever #(CLK_PERIOD/2)    clk     = ~clk;     end
  initial begin mem_clk = 0; #(MEMCLK_PERIOD/2); forever #(MEMCLK_PERIOD/2) mem_clk = ~mem_clk; end
  initial begin dot_clk = 0; #(CLK_PERIOD/2);    forever #(CLK_PERIOD/2)    dot_clk = ~dot_clk; end

  // ---- reset ----
  reg [7:0] rst_ff;
  wire      rst;                 // active-HIGH released (= bench rst); feeds mpeg2video.rst (active-low input but bench drives it this way: see note)
  assign rst = rst_ff[7];
  always @(posedge clk) rst_ff <= {rst_ff[6:0], 1'b1};

  // mem_shim wants active-LOW rst_n in the mem_clk domain. rst (=1 when released)
  // maps directly: rst_n = rst.
  wire rst_n = rst;
  // ddr3_model uses active-HIGH "rst" meaning "in reset" — invert: it is in
  // reset while rst==0.
  wire ddr_rst = ~rst;

  // ---- stream feed (identical to bench testbench.v, plus optional REAL-TIME pacing) ----
  // Default (+knobs off) = 1 byte/27MHz cycle whenever ~busy, byte-identical to the
  // baseline. Pacing knobs model the HW delivery shape the instant feed hides:
  //   +stream_gap=G          G idle cycles after every byte (~27/(G+1) MB/s average;
  //                          G=13 ~= a 2 MB/s real-time DVD-rate feed)
  //   +stream_sector_gap=S   S EXTRA idle cycles every 2048 bytes (the sd_* sector-
  //                          service latency between pulled sector bursts)
  // With a real-time-paced feed the VLD chronically CATCHES the vbuf write head
  // mid-stream (the sim's instant feed keeps the ring far ahead), which is what
  // arms the posted-write RAW staleness window at boot-varying points.
  integer    i;
  reg  [7:0] stream[0:`MAX_STREAM_LENGTH];
  reg  [7:0] stream_data;
  reg        stream_valid;
  wire       busy;
  integer    stream_gap, sector_gap, gap_ctr;

  initial #0 begin
    $readmemh("stream.dat", stream, 0, `MAX_STREAM_LENGTH);
    rst_ff       = 8'b0;
    i            = 0;
    stream_data  = 0;
    stream_valid = 0;
    stream_gap   = 0;
    sector_gap   = 0;
    gap_ctr      = 0;
    if ($value$plusargs("stream_gap=%d", stream_gap));
    if ($value$plusargs("stream_sector_gap=%d", sector_gap));
    if (stream_gap != 0 || sector_gap != 0)
      $display("[tb] stream pacing: gap=%0d/byte sector_gap=%0d/2048B", stream_gap, sector_gap);
  end

  always @(posedge clk)
    if (~rst) begin
      i <= 0; stream_data <= #1 0; stream_valid <= #1 1'b0; gap_ctr <= 0;
    end
    else if (gap_ctr > 0) begin
      gap_ctr <= gap_ctr - 1;
      i <= i; stream_data <= #1 0; stream_valid <= #1 1'b0;
    end
    else if (~busy && (i < `MAX_STREAM_LENGTH) && (^stream[i] !== 1'bx)) begin
      i <= i + 1; stream_data <= #1 stream[i]; stream_valid <= #1 1'b1;
      gap_ctr <= stream_gap + ((((i + 1) % 2048) == 0) ? sector_gap : 0);
    end
    else begin
      i <= i; stream_data <= #1 0; stream_valid <= #1 1'b0;
    end

  // ---- decoder I/O ----
  wire        error, interrupt;
  wire        pixel_en, h_sync, v_sync;
  wire  [7:0] r, g, b, y, u, v;

  wire  [1:0] mem_req_rd_cmd;
  wire [21:0] mem_req_rd_addr;
  wire [63:0] mem_req_rd_dta;
  wire        mem_req_rd_en;
  wire        mem_req_rd_valid;
  wire [63:0] mem_res_wr_dta;
  wire        mem_res_wr_en;
  wire        mem_res_wr_almost_full;
  wire [33:0] testpoint;

  // ---- DDR3/Avalon bus between mem_shim and ddr3_model ----
  wire [28:0] ddr3_addr;
  wire  [7:0] ddr3_burstcnt;
  wire        ddr3_read;
  wire        ddr3_write;
  wire [63:0] ddr3_writedata;
  wire  [7:0] ddr3_byteenable;
  wire [63:0] ddr3_readdata;
  wire        ddr3_readdatavalid;
  wire        ddr3_waitrequest;

  // mem_shim debug
  wire [3:0]  shim_state;
  wire [1:0]  shim_saved_cmd;
  wire        shim_busy;
  wire        shim_ack;
  wire [15:0] shim_rd_count;
  wire [15:0] shim_wr_count;
  wire [15:0] shim_rsp_count;
  wire [15:0] shim_read_pend;

  // ---------------------------------------------------------------------------
  // Decoder (pristine mpeg2fpga mpeg2video.v: active-low rst, no MiSTer probe
  // ports). clk=27, mem_clk=108, dot_clk=27 -> the real CDC ratios.
  // ---------------------------------------------------------------------------
  mpeg2video mpeg2 (
    .clk(clk),
    .mem_clk(mem_clk),
    .dot_clk(dot_clk),
    .rst(rst),
    .stream_data(stream_data),
    .stream_valid(stream_valid),
    .reg_addr(4'b0),
    .reg_wr_en(1'b0),
    .reg_dta_in(32'b0),
    .reg_rd_en(1'b0),
    .reg_dta_out(),
    .busy(busy),
    .error(error),
    .interrupt(interrupt),
    .watchdog_rst(),
    .r(r), .g(g), .b(b), .y(y), .u(u), .v(v),
    .pixel_en(pixel_en), .h_sync(h_sync), .v_sync(v_sync), .c_sync(),
    .mem_req_rd_cmd(mem_req_rd_cmd),
    .mem_req_rd_addr(mem_req_rd_addr),
    .mem_req_rd_dta(mem_req_rd_dta),
    .mem_req_rd_en(mem_req_rd_en),
    .mem_req_rd_valid(mem_req_rd_valid),
    .mem_res_wr_dta(mem_res_wr_dta),
    .mem_res_wr_en(mem_res_wr_en),
    .mem_res_wr_almost_full(mem_res_wr_almost_full),
    .testpoint(testpoint),
    .testpoint_dip(4'h0),
    .testpoint_dip_en(1'b1)
  );

  // ---------------------------------------------------------------------------
  // ADDR_ERR fault injector — transparently sits on the request port (mem_clk)
  // and can rewrite a decoder READ's address to ADDR_ERR to exercise mem_shim's
  // synthetic-ADDR_ERR-read branch / the same-cycle response collision. With no
  // inj_* plusargs it is a pure pass-through (baseline behaviour identical).
  // ---------------------------------------------------------------------------
  wire  [1:0] inj_cmd;
  wire [21:0] inj_addr;
  wire [63:0] inj_dta;
  wire        inj_valid;
  wire        inj_dec_rd_en;
  wire [31:0] inj_count;

  req_inject req_inject_inst (
    .clk(mem_clk),
    .rst_n(rst_n),
    .in_cmd(mem_req_rd_cmd),
    .in_addr(mem_req_rd_addr),
    .in_dta(mem_req_rd_dta),
    .in_valid(mem_req_rd_valid),
    .out_cmd(inj_cmd),
    .out_addr(inj_addr),
    .out_dta(inj_dta),
    .out_valid(inj_valid),
    .shim_rd_en(mem_req_rd_en),
    .dec_rd_en(inj_dec_rd_en),
    .ddr3_readdatavalid(ddr3_readdatavalid),
    .inj_count(inj_count)
  );

  // ---------------------------------------------------------------------------
  // REAL mem_shim (core/MiSTer_MPEG2/rtl/mem_shim.sv). clk = mem_clk (108 MHz).
  // Request inputs come through the injector (addr may be ADDR_ERR-rewritten);
  // everything else is the decoder's signals verbatim.
  // ---------------------------------------------------------------------------
  mem_shim mem_shim_inst (
    .clk(mem_clk),
    .rst_n(rst_n),
    .hard_rst_n(rst_n),
    .mem_req_rd_cmd(inj_cmd),
    .mem_req_rd_addr(inj_addr),
    .mem_req_rd_dta(inj_dta),
    .mem_req_rd_en(mem_req_rd_en),
    .mem_req_rd_valid(inj_valid),
    .mem_res_wr_dta(mem_res_wr_dta),
    .mem_res_wr_en(mem_res_wr_en),
    .mem_res_wr_almost_full(mem_res_wr_almost_full),
    .ddr3_addr(ddr3_addr),
    .ddr3_burstcnt(ddr3_burstcnt),
    .ddr3_read(ddr3_read),
    .ddr3_write(ddr3_write),
    .ddr3_writedata(ddr3_writedata),
    .ddr3_byteenable(ddr3_byteenable),
    .ddr3_readdata(ddr3_readdata),
    .ddr3_readdatavalid(ddr3_readdatavalid),
    .ddr3_waitrequest(ddr3_waitrequest),
    .debug_state(shim_state),
    .debug_saved_cmd(shim_saved_cmd),
    .debug_sdram_busy(shim_busy),
    .debug_sdram_ack(shim_ack),
    .debug_rd_count(shim_rd_count),
    .debug_wr_count(shim_wr_count),
    .debug_rsp_count(shim_rsp_count),
    .debug_read_pend_cycles(shim_read_pend)
  );

  // ---------------------------------------------------------------------------
  // DDR3 / f2sdram behavioral slave. clk = mem_clk; active-HIGH ddr_rst.
  // ---------------------------------------------------------------------------
  ddr3_model ddr3 (
    .clk(mem_clk),
    .rst(ddr_rst),
    .ddr3_addr(ddr3_addr),
    .ddr3_burstcnt(ddr3_burstcnt),
    .ddr3_read(ddr3_read),
    .ddr3_write(ddr3_write),
    .ddr3_writedata(ddr3_writedata),
    .ddr3_byteenable(ddr3_byteenable),
    .ddr3_readdata(ddr3_readdata),
    .ddr3_readdatavalid(ddr3_readdatavalid),
    .ddr3_waitrequest(ddr3_waitrequest)
  );

  // ===========================================================================
  // ADDR_ERR same-cycle COLLISION MONITOR.
  // The hazard ([feed+ADDR_ERR] analysis): on a cycle where mem_shim's S_IDLE
  // takes the synthetic-ADDR_ERR-read branch (sets mem_res_wr_en<=1, dta<=0)
  // AND a real ddr3_readdatavalid lands (line 109/111 want to own the real
  // response), the non-blocking last-write-wins drops the real response.
  //
  // We detect the branch by inspecting mem_shim's request inputs + state:
  //   - direct branch  : state==0, !saved_valid, inj_valid READ to ADDR_ERR
  //   - skid  branch   : state==0, saved_valid READ to ADDR_ERR
  // both gated on !mem_res_wr_almost_full (the branch's own guard). A COLLISION
  // is that branch firing on the SAME cycle ddr3_readdatavalid is high.
  // After the fix patch is applied, the branch defers when readdatavalid is
  // high, so collisions should be detected as "averted" (branch held), not as a
  // dropped response. We count both raw coincidences and whether the decoder
  // tag-checker subsequently $stops.
  // ===========================================================================
  localparam [21:0] ADDR_ERR_TB = 22'h1EFFFF;
  localparam [1:0]  CMD_READ_TB = 2'd2;

  wire shim_state0      = (mem_shim_inst.state == 1'b0);
  wire shim_saved_valid = mem_shim_inst.saved_valid;
  wire shim_not_full    = !mem_res_wr_almost_full;

  // direct ADDR_ERR-read branch reachable this cycle
  wire direct_addrerr_rd = shim_state0 && !shim_saved_valid &&
                           inj_valid && (inj_cmd == CMD_READ_TB) &&
                           (inj_addr == ADDR_ERR_TB) && shim_not_full;
  // skid ADDR_ERR-read branch reachable this cycle
  wire skid_addrerr_rd   = shim_state0 && shim_saved_valid &&
                           (mem_shim_inst.saved_cmd == CMD_READ_TB) &&
                           (mem_shim_inst.saved_addr == ADDR_ERR_TB) && shim_not_full;
  wire addrerr_rd_branch = direct_addrerr_rd || skid_addrerr_rd;

  reg [31:0] collision_count;     // ADDR_ERR-read branch coincides w/ real rdv
  reg [31:0] addrerr_branch_count;
  always @(posedge mem_clk) begin
    if (~rst) begin
      collision_count      <= 0;
      addrerr_branch_count <= 0;
    end else begin
      if (addrerr_rd_branch) addrerr_branch_count <= addrerr_branch_count + 1;
      if (addrerr_rd_branch && ddr3_readdatavalid) begin
        collision_count <= collision_count + 1;
        $display("[tb COLLISION %0t] ADDR_ERR-read branch (direct=%b skid=%b) COINCIDES with real ddr3_readdatavalid=1 readdata=%h state=%b saved_valid=%b (collision #%0d)",
                 $time, direct_addrerr_rd, skid_addrerr_rd, ddr3_readdata,
                 mem_shim_inst.state, mem_shim_inst.saved_valid, collision_count + 1);
      end
    end
  end

  // ===========================================================================
  // tv_out PPM dump (verbatim from bench testbench.v) — drives off dot_clk.
  // ===========================================================================
  integer fp = 0;
  reg [31:0] fname_cnt = "0000";
  integer v_sync_seen = 1;
  integer pixel_count = 0;
  integer img_count = 0;

  wire [11:0]syncgen_horizontal_resolution = mpeg2.syncgen_intf.syncgen_horizontal_resolution;
  wire [11:0]syncgen_horizontal_sync_start = mpeg2.syncgen_intf.syncgen_horizontal_sync_start;
  wire [11:0]syncgen_horizontal_sync_end   = mpeg2.syncgen_intf.syncgen_horizontal_sync_end;
  wire [11:0]syncgen_horizontal_length     = mpeg2.syncgen_intf.syncgen_horizontal_length;
  wire [11:0]syncgen_horizontal_halfline   = mpeg2.syncgen_intf.syncgen_horizontal_halfline;
  wire [11:0]dot_vertical_resolution       = mpeg2.syncgen_intf.dot_vertical_resolution;
  wire [11:0]dot_vertical_sync_start       = mpeg2.syncgen_intf.dot_vertical_sync_start;
  wire [11:0]dot_vertical_sync_end         = mpeg2.syncgen_intf.dot_vertical_sync_end;
  wire [11:0]dot_vertical_length           = mpeg2.syncgen_intf.dot_vertical_length;
  wire       dot_interlaced                = mpeg2.syncgen_intf.dot_interlaced;
  wire       dot_odd_field                 = mpeg2.syncgen_intf.sync_gen.odd_field;

  always @(posedge dot_clk) begin
    if (v_sync) v_sync_seen = 1;
    if (v_sync_seen && pixel_en && (^syncgen_horizontal_length !== 1'bx) && (^dot_vertical_length !== 1'bx)) begin
      if (fp != 0) begin
        while (pixel_count < (syncgen_horizontal_length + 1) * (dot_vertical_length + 1)) begin
          $fwrite(fp, " 48  48  48\n"); pixel_count = pixel_count + 1;
        end
        $fclose(fp);
      end
      v_sync_seen = 0; pixel_count = 0;
      fp = $fopen({"tv_out_", fname_cnt, ".ppm"}, "w");
      if (fname_cnt[7:0] != "9") fname_cnt[7:0] = fname_cnt[7:0] + 1;
      else begin
        fname_cnt[7:0] = "0";
        if (fname_cnt[15:8] != "9") fname_cnt[15:8] = fname_cnt[15:8] + 1;
        else begin
          fname_cnt[15:8] = "0";
          if (fname_cnt[23:16] != "9") fname_cnt[23:16] = fname_cnt[23:16] + 1;
          else begin
            fname_cnt[23:16] = "0";
            if (fname_cnt[31:24] != "9") fname_cnt[31:24] = fname_cnt[31:24] + 1;
            else fname_cnt[31:24] = "0";
          end
        end
      end
      if (fp != 0) begin
        $fwrite(fp, "P3\n");
        $fwrite(fp, "# picture %0d  @ %0t\n", img_count, $time);
        $fwrite(fp, "# horizontal resolution %0d sync_start %0d sync_end %0d length %0d\n", syncgen_horizontal_resolution, syncgen_horizontal_sync_start, syncgen_horizontal_sync_end, syncgen_horizontal_length);
        $fwrite(fp, "# vertical resolution %0d sync_start %0d sync_end %0d length %0d\n", dot_vertical_resolution, dot_vertical_sync_start, dot_vertical_sync_end, dot_vertical_length);
        $fwrite(fp, "# interlaced %0d halfline %0d\n", dot_interlaced, syncgen_horizontal_halfline);
        $fwrite(fp, "# field_parity %0d\n", dot_odd_field);
        img_count = img_count + 1;
        $fwrite(fp, "%5d %5d 255\n", syncgen_horizontal_length + 1, dot_vertical_length + 1);
      end
    end
    if (fp != 0) begin
      if (pixel_en && ((^r === 1'bx) || (^g === 1'bx) || (^b === 1'bx)))
        $fwrite(fp, "    255   0   0\n");
      else if (pixel_en) $fwrite(fp, "%3d %3d %3d\n", r, g, b);
      else if (v_sync || h_sync) $fwrite(fp, "  0   0   0\n");
      else $fwrite(fp, " 48  48  48\n");
      pixel_count = pixel_count + 1;
    end
  end

  // ===========================================================================
  // framestore PPM dump — adapted from bench mem_ctl.v write_framestore, but
  // reads ddr3.mem[] (the slave backing store) instead of mem_ctl.mem[].
  // Triggered on a CMD_WRITE to any FRAME_*_Y base (same trigger as the bench).
  // This proves decode-THROUGH-mem_shim by reading what landed in DDR3.
  // ===========================================================================
`include "mem_codes.v"
`include "vld_codes.v"

  wire [13:0]width                   = mpeg2.vld.horizontal_size;
  wire [13:0]height                  = mpeg2.vld.vertical_size;
  wire [13:0]display_horizontal_size = mpeg2.vld.display_horizontal_size;
  wire [13:0]display_vertical_size   = mpeg2.vld.display_vertical_size;
  wire  [1:0]picture_structure       = mpeg2.vld.picture_structure;
  wire  [1:0]chroma_format           = mpeg2.vld.chroma_format;
  wire  [2:0]picture_coding_type     = mpeg2.vld.picture_coding_type;
  wire       update_picture_buffers  = mpeg2.update_picture_buffers;
  wire [12:0]macroblock_address      = mpeg2.macroblock_address;

  reg [13:0] mb_width, mb_height;
  always @* begin
    mb_width  = (width  + 15) >> 4;
    mb_height = (height + 15) >> 4;
  end

  integer frame_number = 0;
  always @(posedge update_picture_buffers) frame_number = frame_number + 1;

  reg [31:0] fs_cnt = "0000";

  task write_row;
    input [31:0]fpv;
    input [21:0]address;
    reg  [63:0]dta;
    reg signed [7:0]p0,p1,p2,p3,p4,p5,p6,p7;
    reg signed [8:0]q0,q1,q2,q3,q4,q5,q6,q7;
    begin
      dta = ddr3.mem[address];
      {p0,p1,p2,p3,p4,p5,p6,p7} = dta;
      q0={p0[7],p0}+9'sd128; q1={p1[7],p1}+9'sd128; q2={p2[7],p2}+9'sd128; q3={p3[7],p3}+9'sd128;
      q4={p4[7],p4}+9'sd128; q5={p5[7],p5}+9'sd128; q6={p6[7],p6}+9'sd128; q7={p7[7],p7}+9'sd128;
      if (^q0===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q0,q0,q0);
      if (^q1===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q1,q1,q1);
      if (^q2===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q2,q2,q2);
      if (^q3===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q3,q3,q3);
      if (^q4===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q4,q4,q4);
      if (^q5===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q5,q5,q5);
      if (^q6===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q6,q6,q6);
      if (^q7===1'bx) $fwrite(fpv,"   0  127    0 "); else $fwrite(fpv,"%4d %4d %4d ",q7,q7,q7);
      $fwrite(fpv,"\n");
    end
  endtask

  task write_mb;
    input [31:0]fpv;
    input  [2:0]blocks;
    input [21:0]base_address;
    input [15:0]mbw;
    input [15:0]mbh;
    integer h,w,s; reg [21:0]addr;
    begin
      for (w=0; w<2*mbw; w=w+1) begin for (s=0;s<24;s=s+1) $fwrite(fpv," 255"); $fwrite(fpv,"\n"); end
      addr = base_address;
      case (blocks)
        4: for (h=0; h<16*mbh; h=h+1) for (w=0; w<2*mbw; w=w+1) begin write_row(fpv,addr); addr=addr+1; end
        1: for (h=0; h<8*mbh; h=h+1) begin
             for (w=0; w<mbw; w=w+1) begin write_row(fpv,addr); addr=addr+1; end
             for (w=0; w<mbw; w=w+1) begin for (s=0;s<24;s=s+1) $fwrite(fpv," 255"); $fwrite(fpv,"\n"); end
           end
        default $fwrite(fpv,"# chroma format not implemented\n");
      endcase
      for (w=0; w<2*mbw; w=w+1) begin for (s=0;s<24;s=s+1) $fwrite(fpv," 255"); $fwrite(fpv,"\n"); end
    end
  endtask

  task write_framestore;
    reg [32*8:1]fname;
    integer fpv;
    begin
      if ((^mb_width !== 1'bx) && (^mb_height !== 1'bx)) begin
        fname = {"framestore_", fs_cnt, ".ppm"};
        if (fs_cnt[7:0] != "9") fs_cnt[7:0] = fs_cnt[7:0] + 1;
        else begin
          fs_cnt[7:0]="0";
          if (fs_cnt[15:8]!="9") fs_cnt[15:8]=fs_cnt[15:8]+1;
          else begin
            fs_cnt[15:8]="0";
            if (fs_cnt[23:16]!="9") fs_cnt[23:16]=fs_cnt[23:16]+1;
            else begin fs_cnt[23:16]="0"; if (fs_cnt[31:24]!="9") fs_cnt[31:24]=fs_cnt[31:24]+1; else fs_cnt[31:24]="0"; end
          end
        end
        fpv = $fopen(fname, "w");
        if (fpv == 0) begin $display("%m\t*** error opening framestore file ***"); $finish; end
        $fwrite(fpv,"P3\n");
        $fwrite(fpv,"# mpeg2 framestore dump (via mem_shim+ddr3_model) @ %0t\n", $time);
        $display("[tb] dumping framestore to %0s @ %0t (frame %0d)", fname, $time, frame_number);
        $fwrite(fpv,"# frame number %0d\n", frame_number);
        $fwrite(fpv,"# horizontal_size %0d\n", width);
        $fwrite(fpv,"# vertical_size %0d\n", height);
        $fwrite(fpv,"# display_horizontal_size %0d\n", display_horizontal_size);
        $fwrite(fpv,"# display_vertical_size %0d\n", display_vertical_size);
        $fwrite(fpv,"# mb_width %0d\n", mb_width);
        $fwrite(fpv,"# mb_height %0d\n", mb_height);
        if (picture_structure==FRAME_PICTURE) $fwrite(fpv,"# picture_structure frame picture\n");
        else $fwrite(fpv,"# picture_structure field picture\n");
        case (picture_coding_type)
          I_TYPE:  begin $fwrite(fpv,"# picture_coding_type I\n"); $display("[tb] framestore pic_type I (frame %0d)", frame_number); end
          P_TYPE:  begin $fwrite(fpv,"# picture_coding_type P\n"); $display("[tb] framestore pic_type P (frame %0d)", frame_number); end
          B_TYPE:  begin $fwrite(fpv,"# picture_coding_type B\n"); $display("[tb] framestore pic_type B (frame %0d)", frame_number); end
          default: begin $fwrite(fpv,"# picture_coding_type %0d\n", picture_coding_type); end
        endcase
        if (chroma_format==CHROMA420) $fwrite(fpv,"# chroma_format 4:2:0\n");
        else $fwrite(fpv,"# chroma_format %0d\n", chroma_format);
        $fwrite(fpv,"%0d %0d 255\n", width, height*9+26);
        write_mb(fpv,4,FRAME_0_Y,  mb_width,mb_height);
        write_mb(fpv,1,FRAME_0_CR, mb_width,mb_height);
        write_mb(fpv,1,FRAME_0_CB, mb_width,mb_height);
        write_mb(fpv,4,FRAME_1_Y,  mb_width,mb_height);
        write_mb(fpv,1,FRAME_1_CR, mb_width,mb_height);
        write_mb(fpv,1,FRAME_1_CB, mb_width,mb_height);
        write_mb(fpv,4,FRAME_2_Y,  mb_width,mb_height);
        write_mb(fpv,1,FRAME_2_CR, mb_width,mb_height);
        write_mb(fpv,1,FRAME_2_CB, mb_width,mb_height);
        write_mb(fpv,4,FRAME_3_Y,  mb_width,mb_height);
        write_mb(fpv,1,FRAME_3_CR, mb_width,mb_height);
        write_mb(fpv,1,FRAME_3_CB, mb_width,mb_height);
        write_mb(fpv,4,OSD,        mb_width,mb_height);
        $fwrite(fpv,"# not truncated\n");
        $fclose(fpv);
      end
    end
  endtask

  // Trigger framestore dump on a write to a FRAME_*_Y base, observed at the
  // DECODER request port (mem_req_rd_*) the cycle the FIFO presents it to the
  // shim. This is the same event the bench used (it watched mem_req at mem_ctl).
  always @(posedge mem_clk)
    if (rst && mem_req_rd_valid && (mem_req_rd_cmd == CMD_WRITE) &&
        ((mem_req_rd_addr==FRAME_0_Y)||(mem_req_rd_addr==FRAME_1_Y)||
         (mem_req_rd_addr==FRAME_2_Y)||(mem_req_rd_addr==FRAME_3_Y)))
      write_framestore;

  // ===========================================================================
  // PROGRESS WATCHDOG + STALL LOCALIZATION.
  // Tracks decoder forward progress via macroblock_address and ddr3 responses.
  // If neither advances for WATCHDOG_CYCLES mem_clk ticks, dump a full report
  // and finish. Also a hard MAX_CYCLES cap so the run always terminates.
  // ===========================================================================
  reg  [12:0] last_mb;
  reg  [31:0] last_rsp;
  reg  [63:0] idle_cycles;
  reg  [63:0] total_cycles;
  integer     watchdog_cycles;
  integer     max_cycles;
  integer     stall_dumped;

  initial begin
    last_mb = 13'h1fff; last_rsp = 0; idle_cycles = 0; total_cycles = 0; stall_dumped = 0;
    watchdog_cycles = 2000000;   // ~18.5 ms @108MHz of no progress => stall
    max_cycles      = 80000000;  // hard cap
    if ($value$plusargs("wd_cycles=%d", watchdog_cycles));
    if ($value$plusargs("max_cycles=%d", max_cycles));
  end

  task dump_stall_report;
    input [255:0] reason;
    begin
      $display("================================================================");
      $display("[tb] STALL/END REPORT (%0s) @ %0t  total_cycles=%0d idle_cycles=%0d", reason, $time, total_cycles, idle_cycles);
      $display("[tb]   decoder: busy=%b error=%b macroblock_address=%0d (last=%0d)", busy, error, macroblock_address, last_mb);
      $display("[tb]   decoder: picture_coding_type=%0d frame_number=%0d width=%0d height=%0d", picture_coding_type, frame_number, width, height);
      $display("[tb]   req port: cmd=%0d addr=%h rd_en=%b rd_valid=%b", mem_req_rd_cmd, mem_req_rd_addr, mem_req_rd_en, mem_req_rd_valid);
      $display("[tb]   res port: wr_en=%b almost_full=%b dta=%h", mem_res_wr_en, mem_res_wr_almost_full, mem_res_wr_dta);
      $display("[tb]   mem_shim: state(debug)=%h saved_cmd=%0d sdram_busy(wait)=%b sdram_ack=%b", shim_state, shim_saved_cmd, shim_busy, shim_ack);
      $display("[tb]   mem_shim: rd_count=%0d wr_count=%0d rsp_count=%0d", shim_rd_count, shim_wr_count, shim_rsp_count);
      $display("[tb]   inject:   inj_count=%0d addrerr_branches=%0d collisions=%0d", inj_count, addrerr_branch_count, collision_count);
      $display("[tb]   ddr3 bus: read=%b write=%b waitrequest=%b readdatavalid=%b addr=%h", ddr3_read, ddr3_write, ddr3_waitrequest, ddr3_readdatavalid, ddr3_addr);
      $display("[tb]   framestore_response.state probe: see decoder $display traces above");
      // Decoder-internal localization (hierarchical probes; Verilator resolves these):
      $display("[tb]   stream feed: i=%0d (of %0d) stream_valid=%b busy=%b gap_ctr=%0d", i, `MAX_STREAM_LENGTH, stream_valid, busy, gap_ctr);
      $display("[tb]   vbuf: wr_addr=%h rd_addr=%h empty=%b full=%b (framestore_request)",
               mpeg2.framestore.framestore_request.vbuf_wr_addr,
               mpeg2.framestore.framestore_request.vbuf_rd_addr,
               mpeg2.framestore.framestore_request.vbuf_empty,
               mpeg2.framestore.framestore_request.vbuf_full);
      $display("[tb]   vbr/vbw: do_vbr=%b vbr_wr_full=%b vbr_wr_almost_full=%b vbw_rd_valid=%b vbw_rd_empty=%b",
               mpeg2.framestore.framestore_request.do_vbr,
               mpeg2.framestore.framestore_request.vbr_wr_full,
               mpeg2.framestore.framestore_request.vbr_wr_almost_full,
               mpeg2.framestore.framestore_request.vbw_rd_valid,
               mpeg2.framestore.framestore_request.vbw_rd_empty);
      $display("[tb]   getbits: state=%0d cursor=%0d vid_in_rd_en=%b vid_in_rd_valid=%b getbits_valid=%b",
               mpeg2.getbits_fifo.state, mpeg2.getbits_fifo.cursor,
               mpeg2.getbits_fifo.vid_in_rd_en, mpeg2.getbits_fifo.vid_in_rd_valid,
               mpeg2.getbits_fifo.getbits_valid);
      $display("[tb]   vld gates: vld_en=%b wait_state=%b rld_wr_almost_full=%b mvec_wr_almost_full=%b motcomp_busy=%b",
               mpeg2.vld_en, mpeg2.getbits_fifo.wait_state,
               mpeg2.getbits_fifo.rld_wr_almost_full, mpeg2.getbits_fifo.mvec_wr_almost_full,
               mpeg2.getbits_fifo.motcomp_busy);
      $display("[tb]   mc/recon fifos: fwd_addr_empty=%b fwd_dta_afull=%b bwd_addr_empty=%b bwd_dta_afull=%b recon_rd_empty=%b vbr_rd_aempty=%b tag_afull=%b req_afull=%b",
               mpeg2.framestore.framestore_request.fwd_rd_addr_empty,
               mpeg2.framestore.framestore_request.fwd_wr_dta_almost_full,
               mpeg2.framestore.framestore_request.bwd_rd_addr_empty,
               mpeg2.framestore.framestore_request.bwd_wr_dta_almost_full,
               mpeg2.framestore.framestore_request.recon_rd_empty,
               mpeg2.framestore.framestore_request.vbr_rd_almost_empty,
               mpeg2.framestore.framestore_request.tag_wr_almost_full,
               mpeg2.framestore.framestore_request.mem_req_wr_almost_full);
      $display("[tb]   fwd_reader: wr_dta_full=%b rd_dta_valid=%b rd_dta_en=%b rd_dta_empty=%b rd_dta_aempty=%b",
               mpeg2.fwd_reader.wr_dta_full,
               mpeg2.fwd_reader.rd_dta_valid, mpeg2.fwd_reader.rd_dta_en,
               mpeg2.fwd_reader.rd_dta_empty, mpeg2.fwd_reader.rd_dta_almost_empty);
      $display("[tb]   recon gates: state=%0d dst_valid_0=%b motion_fwd_0=%b motion_bwd_0=%b write_recon_0=%b fwd_row_valid=%b bwd_row_valid=%b idct_row_valid=%b recon_wr_afull=%b",
               mpeg2.motcomp.motcomp_recon.state,
               mpeg2.motcomp.motcomp_recon.dst_valid_0,
               mpeg2.motcomp.motcomp_recon.motion_forward_0,
               mpeg2.motcomp.motcomp_recon.motion_backward_0,
               mpeg2.motcomp.motcomp_recon.write_recon_0,
               mpeg2.motcomp.motcomp_recon.fwd_row_0_valid,
               mpeg2.motcomp.motcomp_recon.bwd_row_0_valid,
               mpeg2.motcomp.motcomp_recon.idct_row_0_valid,
               mpeg2.motcomp.motcomp_recon.recon_wr_almost_full);
      $display("[tb]   word audit: fifo_writes=%0d delivered=%0d addr_err=%0d ddr_cmds(rd+wr)=%0d => LOST_AT_SHIM=%0d UNDELIVERED=%0d",
               cnt_fifo_wr, cnt_valid, cnt_valid_ae, ddr3.rd_issued + ddr3.wr_issued,
               cnt_valid - cnt_valid_ae - (ddr3.rd_issued + ddr3.wr_issued),
               cnt_fifo_wr - cnt_valid);
      $display("[tb]   picbuf: busy=%b out_frame=%0d out_valid=%b out_rd=%b cur_frame=%0d fwd_ref=%0d bwd_ref=%0d",
               mpeg2.motcomp.motcomp_addrgen.picbuf.picbuf_busy,
               mpeg2.motcomp.motcomp_addrgen.picbuf.output_frame,
               mpeg2.motcomp.motcomp_addrgen.picbuf.output_frame_valid,
               mpeg2.motcomp.motcomp_addrgen.picbuf.output_frame_rd,
               mpeg2.motcomp.motcomp_addrgen.picbuf.current_frame,
               mpeg2.motcomp.motcomp_addrgen.picbuf.forward_reference_frame,
               mpeg2.motcomp.motcomp_addrgen.picbuf.backward_reference_frame);
      $display("[tb]   resample: addrgen_state=%0d busy=%b disp_addr_full=%b disp_addr_afull=%b resample_wr_afull=%b",
               mpeg2.resample.resample_addrgen.state, mpeg2.resample.resample_addrgen.busy,
               mpeg2.resample.resample_addrgen.disp_wr_addr_full,
               mpeg2.resample.resample_addrgen.disp_wr_addr_almost_full,
               mpeg2.resample.resample_addrgen.resample_wr_almost_full);
      $display("[tb]   resp router: mem_res_rd_empty=%b tag_rd_empty=%b",
               mpeg2.framestore.framestore_response.mem_res_rd_empty,
               mpeg2.framestore.framestore_response.tag_rd_empty);
      // === DISPLAY-ACK WEDGE micro-root discriminators ===
      // rdta_state 6==STATE_READY (pixel-backpressure) ; 1..4==STATE_RD_OSD/Y/U/V (data starvation / pairing desync)
      $display("[tb]   resample_dta: state=%0d fifo_read=%b fifo_valid=%b disp_fwft_valid=%b disp_fwft_rd_en=%b resample_fwft_valid=%b",
               mpeg2.resample.resample_dta.state,
               mpeg2.resample.resample_dta.fifo_read,
               mpeg2.resample.resample_dta.fifo_valid,
               mpeg2.resample.resample_dta.disp_fwft_valid,
               mpeg2.resample.resample_dta.disp_fwft_rd_en,
               mpeg2.resample.resample_dta.resample_fwft_valid);
      $display("[tb]   resample_bilinear: state=%0d pixel_wr_afull=%b fifo_read=%b pixel_wr_en=%b",
               mpeg2.resample.resample_bilinear.state,
               mpeg2.resample.resample_bilinear.pixel_wr_almost_full,
               mpeg2.resample.resample_bilinear.fifo_read,
               mpeg2.resample.resample_bilinear.pixel_wr_en);
      $display("[tb]   disp_reader: rd_dta_empty=%b rd_dta_aempty=%b rd_dta_valid=%b rd_dta_en=%b wr_dta_full=%b wr_dta_afull=%b rd_addr_empty=%b",
               mpeg2.disp_reader.rd_dta_empty,
               mpeg2.disp_reader.rd_dta_almost_empty,
               mpeg2.disp_reader.rd_dta_valid,
               mpeg2.disp_reader.rd_dta_en,
               mpeg2.disp_reader.wr_dta_full,
               mpeg2.disp_reader.wr_dta_almost_full,
               mpeg2.disp_reader.rd_addr_empty);
      $display("[tb]   pixel_queue: cnt_wr=%0d cnt_rd=%0d wr_afull=%b wr_full=%b rd_empty=%b rd_valid=%b rd_underflow=%b do_disp=%b disp_wr_dta_afull=%b",
               cnt_pixel_wr, cnt_pixel_rd,
               mpeg2.pixel_wr_almost_full, mpeg2.pixel_wr_full, mpeg2.pixel_rd_empty_pqueue,
               mpeg2.pixel_rd_valid_pqueue, mpeg2.pixel_rd_underflow_pqueue,
               mpeg2.framestore.framestore_request.do_disp,
               mpeg2.framestore.framestore_request.disp_wr_dta_almost_full);
      // === RASTER-CONSUMER (mixer) — THE ORIGIN of the backpressure cascade ===
      // mst: 0=INIT 1=WAIT 2=FIRST_PIXEL 4=PIXEL 6=LAST_PIXEL. If h_pos/v_pos are advancing but prd_en stuck 0
      // in WAIT, display_first_pixel never re-matches the frozen pos0 => the underrun re-align trap.
      $display("[tb]   mixer: state=%0d pixel_rd_en=%b first_pixel_read=%b display_first_pixel=%b position_in_0=%0d h_pos=%0d v_pos=%0d pixel_en_in=%b h_sync_in=%b v_sync_in=%b",
               mpeg2.mixer.state, mpeg2.mixer.pixel_rd_en,
               mpeg2.mixer.first_pixel_read, mpeg2.mixer.display_first_pixel,
               mpeg2.mixer.position_in_0, mpeg2.mixer.h_pos, mpeg2.mixer.v_pos,
               mpeg2.mixer.pixel_en_in, mpeg2.mixer.h_sync_in, mpeg2.mixer.v_sync_in);
      ddr3.report_counts;
      $display("================================================================");
    end
  endtask

  // word-conservation audit: any presented-but-unconsumed word at the shim = loss
  reg [31:0] cnt_fifo_wr;      // words written INTO mem_request_fifo (27MHz side)
  reg [31:0] cnt_valid;        // valid pulses at the shim boundary (words delivered)
  reg [31:0] cnt_valid_ae;     // delivered words that are ADDR_ERR (filtered, not issued)
  always @(posedge clk)
    if (~rst) cnt_fifo_wr <= 0;
    else if (mpeg2.framestore.mem_req_wr_en) cnt_fifo_wr <= cnt_fifo_wr + 1;
  always @(posedge mem_clk)
    if (~rst) begin cnt_valid <= 0; cnt_valid_ae <= 0; end
    else if (mem_req_rd_valid) begin
      cnt_valid <= cnt_valid + 1;
      if (mem_req_rd_addr == 22'h1EFFFF) cnt_valid_ae <= cnt_valid_ae + 1;
    end

  // pixel-path liveness counters (DISPLAY-ACK WEDGE forensics). Sampled on mem_clk;
  // the 108/27 clock-ratio overcounts by ~4x but that is irrelevant — we only read these
  // as "advancing (consumer alive)" vs "frozen (consumer dead)" between heartbeats.
  reg [31:0] cnt_pixel_wr;   // resample_bilinear -> pixel_queue writes  (producer)
  reg [31:0] cnt_pixel_rd;   // pixel_queue -> mixer valid reads         (raster consumer liveness)
  always @(posedge mem_clk)
    if (~rst) begin cnt_pixel_wr <= 0; cnt_pixel_rd <= 0; end
    else begin
      if (mpeg2.pixel_wr_en)            cnt_pixel_wr <= cnt_pixel_wr + 1;
      if (mpeg2.pixel_rd_valid_pqueue)  cnt_pixel_rd <= cnt_pixel_rd + 1;
    end

  // trajectory trace: one line per ~1ms of sim time (108K mem cycles)
  reg [31:0] traj_ctr;
  always @(posedge mem_clk) begin
    if (~rst) traj_ctr <= 0;
    else begin
      traj_ctr <= traj_ctr + 1;
      if (traj_ctr % 108000 == 0) begin
        $display("[traj %0t] i=%0d mb=%0d fr=%0d wr=%h rd=%h vbw_empty=%b busy=%b flush=%b rdcnt=%0d",
                 $time, i, macroblock_address, frame_number,
                 mpeg2.framestore.framestore_request.vbuf_wr_addr,
                 mpeg2.framestore.framestore_request.vbuf_rd_addr,
                 mpeg2.framestore.framestore_request.vbw_rd_empty,
                 busy, mpeg2.flush_vbuf, shim_rd_count);
        // DISPLAY-path liveness: pwr/prd advancing => consumer alive; rdta/bil states + queue occupancy
        $display("[pix  %0t] pwr=%0d prd=%0d pix_wr_afull=%b pix_wr_full=%b pix_rd_empty=%b rdta_st=%0d bil_st=%0d disp_wrdta_full=%b do_disp=%b",
                 $time, cnt_pixel_wr, cnt_pixel_rd,
                 mpeg2.pixel_wr_almost_full, mpeg2.pixel_wr_full, mpeg2.pixel_rd_empty_pqueue,
                 mpeg2.resample.resample_dta.state, mpeg2.resample.resample_bilinear.state,
                 mpeg2.disp_reader.wr_dta_full,
                 mpeg2.framestore.framestore_request.do_disp);
        // RASTER-CONSUMER (mixer) liveness: is the raster (h_pos/v_pos) still advancing, and why is the mixer parked?
        $display("[mix  %0t] mst=%0d prd_en=%b fpx_rd=%b disp_fpx=%b pos0=%0d h_pos=%0d v_pos=%0d px_en=%b vsync=%b",
                 $time, mpeg2.mixer.state, mpeg2.mixer.pixel_rd_en,
                 mpeg2.mixer.first_pixel_read, mpeg2.mixer.display_first_pixel,
                 mpeg2.mixer.position_in_0, mpeg2.mixer.h_pos, mpeg2.mixer.v_pos,
                 mpeg2.mixer.pixel_en_in, mpeg2.mixer.v_sync_in);
      end
    end
  end

  always @(posedge mem_clk) begin
    if (~rst) begin
      last_mb <= 13'h1fff; last_rsp <= 0; idle_cycles <= 0; total_cycles <= 0;
    end else begin
      total_cycles <= total_cycles + 1;
      // progress = macroblock advanced OR a ddr3 response arrived OR a frame write happened
      if ((macroblock_address !== last_mb && (^macroblock_address !== 1'bx)) ||
          (ddr3.rd_responded !== last_rsp) ||
          ddr3_readdatavalid || mem_res_wr_en) begin
        idle_cycles <= 0;
        last_mb  <= (^macroblock_address !== 1'bx) ? macroblock_address : last_mb;
        last_rsp <= ddr3.rd_responded;
      end else begin
        idle_cycles <= idle_cycles + 1;
      end

      if (idle_cycles > watchdog_cycles && !stall_dumped) begin
        stall_dumped = 1;
        dump_stall_report("WATCHDOG-NO-PROGRESS");
        $finish;
      end
      if (total_cycles > max_cycles) begin
        dump_stall_report("MAX-CYCLES-CAP");
        $finish;
      end
    end
  end

  // Periodic heartbeat so a detached run shows liveness in the log.
  reg [63:0] hb;
  always @(posedge mem_clk) begin
    if (~rst) hb <= 0;
    else begin
      hb <= hb + 1;
      if (hb[19:0] == 20'd0)
        $display("[tb hb %0t] mb=%0d busy=%b shim_state=%h rd=%0d rsp=%0d ddr_wait=%b idle=%0d",
                 $time, macroblock_address, busy, shim_state, shim_rd_count, shim_rsp_count, ddr3_waitrequest, idle_cycles);
    end
  end

endmodule
/* not truncated */
