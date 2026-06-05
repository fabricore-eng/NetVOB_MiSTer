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

set_min_delay -from [get_keepers {*mem_shim:mem_shim_inst|ram_write *mem_shim:mem_shim_inst|ram_address[*]}] \
              -to   [get_keepers {*f2sdram_safe_terminator*|state_write *f2sdram_safe_terminator*|write_address_latch[*] *f2sdram_safe_terminator*|write_burstcount_latch[*] *f2sdram_safe_terminator*|write_terminate_counter[*]}] \
              3.0
