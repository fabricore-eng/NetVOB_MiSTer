# sta_clk_check.tcl — post-fit STA triage for the mpeg2fpga core's PLL domains.
# Answers: are there REAL intra-domain failing paths (clk_mem->clk_mem / clk_sys->clk_sys)
# vs only FALSE cross-domain paths (clk_sys<->clk_mem FIFO crossing, unrelated PLLs)?
# Run on dell in the Quartus image:
#   docker run --rm -v "$PWD":/work -w /work --entrypoint quartus_sta \
#     raetro/quartus:17.0 -t tools/build/sta_clk_check.tcl
project_open mpeg2fpga
create_timing_netlist -model slow
read_sdc
update_timing_netlist

set mem {emu|sys_pll|altera_pll_i|general[1].gpll~PLL_OUTPUT_COUNTER|divclk}
set sys {emu|sys_pll|altera_pll_i|general[0].gpll~PLL_OUTPUT_COUNTER|divclk}

proc worst {label fromc toc} {
    set ps [get_timing_paths -setup -from_clock $fromc -to_clock $toc -npaths 1 -nworst 1]
    set n [get_collection_size $ps]
    if {$n == 0} { puts "$label : (no paths)"; return }
    foreach_in_collection p $ps {
        puts "$label : slack = [get_path_info $p -slack]"
    }
}

puts "##### CLK-DOMAIN TRIAGE #####"
worst "INTRA clk_mem(108)->clk_mem" $mem $mem
worst "INTRA clk_sys(27)->clk_sys"  $sys $sys
worst "XING clk_sys->clk_mem (FIFO)" $sys $mem
worst "XING clk_mem->clk_sys (FIFO)" $mem $sys

puts "##### TOP clk_mem->clk_mem PATHS (find the worst REAL one: levels>1) #####"
# levels==1 with a huge negative slack = a clock-relationship ARTIFACT, ignore. A REAL
# failing DDR-interface path has levels>1 AND negative slack -> then lowering clk_mem is
# justified. If every REAL path closes (slack>0) -> the lost responses are a handshake/FIFO
# corner, NOT raw timing, and lowering the clock won't help (573's prove-it-first discipline).
set ps [get_timing_paths -setup -from_clock $mem -to_clock $mem -npaths 12 -nworst 12]
foreach_in_collection p $ps {
    set lv [get_path_info $p -num_logic_levels]
    set sl [get_path_info $p -slack]
    set tag artifact?; if {$lv > 1} { set tag REAL }
    puts "slack=$sl levels=$lv $tag  FROM=[get_node_info -name [get_path_info $p -from]]  TO=[get_node_info -name [get_path_info $p -to]]"
}
# Also: any failing setup path ENDING in mem_shim (the f2sdram readdata/response capture)?
puts "--- worst setup paths INTO mem_shim (the DDR-interface capture) ---"
set mp [get_timing_paths -setup -to {*mem_shim*} -npaths 6 -nworst 6]
foreach_in_collection p $mp {
    puts "slack=[get_path_info $p -slack] levels=[get_path_info $p -num_logic_levels]  FROM=[get_node_info -name [get_path_info $p -from]]  TO=[get_node_info -name [get_path_info $p -to]]"
}
puts "##### END #####"
