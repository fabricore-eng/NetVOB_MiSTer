# mpeg2fpga_holdfix.sdc — HW hold-margin fix for the f2sdram command/addr hop (2026-06-05)
#
# Problem: the mem_shim -> f2sdram_safe_terminator command/addr hop (ram_write -> state_write,
# ram_address -> *_latch) is a TRUE intra-clock reg->reg HOLD path (mem_shim and the terminator
# are BOTH on clk_mem == DDRAM_CLK by design — emu.sv deliberately uses clk_mem for both to
# eliminate the f2sdram CDC). Its HOLD margin is only ~+0.64ns: STA-positive (passes), but so
# tight that adding logic near the bridge re-rolls the placement and tips it functional -> the
# board WEDGES (bridge never accepts a command: J stuck, P==RP==0). A pipeline register can't fix
# a reg->reg HOLD hop (it splits SETUP, not hold).
#
# Fix (cockpit + 573, same-domain branch): force the fitter to INSERT delay on these fast/min
# paths via set_min_delay -> more hold margin -> placement stops tipping it. Viable here because
# the core is only ~36% ALM (the fitter has routing room to add delay; 573 couldn't do this at 98%).
# Setup has ~+4.9ns slack to absorb the added delay for free. set_min_delay is Web-Edition allowed
# (unlike LogicLock). Target ALL THREE terminators (ram1/ram2/vbuf) because the marginal hop MOVES
# between them on a re-roll — constrain the whole group or the wedge just relocates.
#
# Value 3.0ns ~= 1-1.5ns above the current short-path delay (cockpit to confirm/refine the exact
# value + node list from the post-build .fit.rpt and verify the hold margin actually GREW).

# [DISABLED 2026-06-05, replaced by LCELL hold-delay] set_min_delay -from [get_keepers {*mem_shim:mem_shim_inst|ram_write *mem_shim:mem_shim_inst|ram_address[*]}] \
# [DISABLED 2026-06-05, replaced by LCELL hold-delay]               -to   [get_keepers {*f2sdram_safe_terminator*|state_write *f2sdram_safe_terminator*|write_address_latch[*] *f2sdram_safe_terminator*|write_burstcount_latch[*] *f2sdram_safe_terminator*|write_terminate_counter[*]}] \
# [DISABLED 2026-06-05, replaced by LCELL hold-delay]               3.0

# set_min_delay disabled — the hold margin is now added by a fixed keep'd LCELL delay chain
# on the command/addr in mem_shim.sv (cockpit lever b), which grows hold without over-detouring setup.

# ===== LCELL LEVER ABANDONED — full-path proof, 2026-06-06 =====
# report_timing -detail full_path on the re-constrained build proved the keep'd LCELL chain injects
# ~78ns of pure INTERCONNECT routing: 6 hops of 10-15ns each between scattered keep'd buffers
# (u_dly_wr0..dly_wr2). Result: setup -76.198 (VIOLATED, data delay 84.820ns on a 9.259ns path).
# Unconstrained the same chain WEDGED. Both states non-viable -> LCELL hold-delay lever is DEAD.
#
# Reframe: a bare reg->reg hop ram_write->f2sdram_safe_terminator lives entirely within clk_mem
# (general[1], 9.259ns) and is therefore AUTO-CONSTRAINED by that clock — it was NEVER truly
# unconstrained. cockpit's "-89 unconstrained artifact" was created BY the lcell (the keep'd buffer
# chain broke STA's clock association on that net), not an intrinsic property of the path. On the bare
# working-baseline mem_shim (217b0b6b) this path measured setup +4.9 / hold +0.64 — STA-clean — and
# that build PARTIALLY DECODED (the bars-region breakthrough). So NO timing lever is needed here.
#
# Therefore: lcell removed from mem_shim.sv (restored 217b0b6b). With no lcell, ram_write->terminator
# is a clean intra-clk_mem reg->reg path that set_min/max_delay can constrain on ALL dests (incl
# write_burstcounter — the lcell-created intermediate startpoint that blocked the -from reach is gone).
#
# ===== cockpit's BRACKET (b), 2026-06-06 — no lcell, hedge both axes =====
# set_min_delay 3.0 targets the PROVEN-nonwedge hold (+1.054) by forcing >=3.0ns onto the path's MIN
# (fast) corner; set_max_delay 9.259 (= clk_mem period) caps the added delay so it can't over-detour
# into the setup-negative region (3.0-ALONE overshot to setup -1.75). Net: delay forced into
# [3.0, 9.259] -> hold ~+1.05, setup ~positive. No keep'd buffers -> no cut artifact. Re-verify next
# build: ram_write->terminator CONSTRAINED on ALL dests incl burstcounter (no -76), setup>+0.5, hold>0.
set_min_delay 3.0   -from [get_keepers {*mem_shim:mem_shim_inst|ram_write}] -to [get_keepers {*f2sdram_safe_terminator*}]
set_max_delay 9.259 -from [get_keepers {*mem_shim:mem_shim_inst|ram_write}] -to [get_keepers {*f2sdram_safe_terminator*}]
