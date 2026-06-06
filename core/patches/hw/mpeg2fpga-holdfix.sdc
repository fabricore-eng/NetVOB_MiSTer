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

# ===== Re-constrain the LCELL'd command-accept path (cockpit, 2026-06-06) =====
# The keep'd lcell on ram_write DROPPED STA's auto-constraint on ram_write->terminator -> that path
# went UNCONSTRAINED -> the fitter didn't time/place it -> WEDGE (even at hold +2.31). Root cause was
# CONSTRAINED-vs-UNCONSTRAINED, not hold-magnitude. Re-impose the normal single-cycle requirement so the
# fitter times+places it deterministically (period general[1]=9.259ns @108MHz=clk_mem). All 12 worst -89
# paths are ram_write-only; ram_address/ram_read stay constrained (+2.3) so they DON'T need this.
set_max_delay 9.259 -from [get_keepers {*mem_shim:mem_shim_inst|ram_write}] -to [get_keepers {*f2sdram_safe_terminator*}]
set_min_delay 0.5   -from [get_keepers {*mem_shim:mem_shim_inst|ram_write}] -to [get_keepers {*f2sdram_safe_terminator*}]
