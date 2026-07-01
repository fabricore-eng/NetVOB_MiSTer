#!/usr/bin/env tclsh
# =============================================================================
# write_wedge_stp.tcl -- GENERATE the SignalTap .stp for the dvd MPEG-2 core's
# f2sdram WRITE-PATH WEDGE probe (2026-07-01).
#
# QUESTION (the one capture this answers): the age-gate build wedges during the
# framestore CLEAR -- pure writes, P:0000, wr_count frozen (241..8511, jitters),
# U:1 (ddr3_waitrequest STUCK high). WHICH path is marginal?
#   * command-OUTPUT (ram_write/ram_address mis-captured at the bridge) -> a
#     simpler output-only register fixes it.
#   * waitrequest-INPUT (the shim mis-reads the bridge's ready) -> needs the
#     full 2-deep skid.
#   * BRIDGE-side (FIFO full / DDR write drain dead) -> registration won't help,
#     only placement / back-annotation will.
# DISCRIMINATOR from the capture: watch the RAW ddr3_waitrequest (st_waitreq)
# over the wedge ONSET. Toggle-then-stick correlated with wr_count/refresh =
# bridge FIFO/refresh (bridge-side). Sticks the instant a specific write is
# presented, with ram_write/state clean = a command-capture glitch. And confirm
# the shim is cleanly presenting a valid write (ram_write=1, state=1) with NO
# reads (ram_read=0, outstanding=0) when it sticks.
#
# PROBE = EXISTING mem_shim registers + ONE observation reg (st_waitreq =
# registered raw ddr3_waitrequest, added in mem_shim.sv for this capture; the
# input can't be tapped directly -- gate #1 needs a real registered net). Per
# the probe-wedge lesson, tapping our own regs (not bridge-adjacent observers,
# not pin taps) minimizes placement re-roll.
#
# Hierarchy (verified from emu.sv + the cap6 fit.rpt, 2026-06-25):
#   sys_top (top) -> emu:emu (rtl/emu.sv) -> mem_shim:mem_shim_inst (emu.sv:425)
# Capture clock = clk_mem (108 MHz, sys_pll outclk_1). PLL OUTPUT NET (gate #4).
#
# USAGE:   tclsh write_wedge_stp.tcl [out.stp]   (default write_wedge.stp here)
# VALIDATE (seconds, no build): scp to dell, open_session in raetro/quartus:17.0
#   -> expect OPEN-OK (RUNBOOK step 0). Real gate is the build + the heisenbug
#   check (the instrumented build MUST still wedge, else no capture).
# =============================================================================

# --- hierarchy -------------------------------------------------------------
set MS "emu:emu|mem_shim:mem_shim_inst"

# Capture clock: clk_mem = sys_pll outclk_1 (108 MHz). PLL output net.
set CLOCK_NODE {emu:emu|sys_pll:sys_pll|altera_pll:altera_pll_i|outclk_wire[1]}

# JTAG identity (overridden at capture time after live jtagconfig discovery).
set JTAG_CHAIN  "DE-SoC \[1-4\]"
set JTAG_DEVICE "@2: 5CSEBA6(.|ES)/5CSEMA6/.. (0x02D020DD)"

set INSTANCE_NAME "auto_signaltap_0"
set SS_NAME       "ss_write_wedge"
set TRIG_NAME     "trig_write_wedge"

# depth 8192 (~76 us @108MHz), POST position (~7/8 PRECEDES the trigger) so the
# whole write CLEAR run -> waitrequest stick -> wedge latch lead-in is captured.
set SAMPLE_DEPTH 8192
set TRIGGER_POSITION "post"
set STORAGE_MODE "continuous"

# --- watch list  {name width}  (width>1 expands name[0..width-1]) ----------
# 1+1+1+1+1 + 16 + 9 + 3 = 33 data bits.
set NODES [list \
    [list "$MS|ram_write"          1] \
    [list "$MS|ram_read"           1] \
    [list "$MS|state"              1] \
    [list "$MS|st_waitreq"         1] \
    [list "$MS|wedged"             1] \
    [list "$MS|wr_count"          16] \
    [list "$MS|wr_stall_cnt"       9] \
    [list "$MS|outstanding_reads"  3] \
]

# --- trigger: fire when the wedge latches (mem_shim wedged flag) ------------
set TRIGGER_TERMS [list [list "$MS|wedged" high]]

# ===========================================================================
# generation -- no user-serviceable parts below (573 silicon-proven schema).
# ===========================================================================
set out "write_wedge.stp"
if {$argc >= 1} { set out [lindex $argv 0] }

set BITS {}
foreach n $NODES {
    lassign $n name width
    if {$width <= 1} { lappend BITS $name } else {
        for {set i 0} {$i < $width} {incr i} { lappend BITS "${name}\[$i\]" }
    }
}
set NBITS [llength $BITS]

array set TPAT {}
foreach t $TRIGGER_TERMS { lassign $t tn tp ; set TPAT($tn) $tp }
foreach tn [array names TPAT] {
    if {[lsearch -exact $BITS $tn] < 0} {
        puts stderr "FATAL: trigger node not in watch list: $tn"; exit 1
    }
}

proc xesc {s} { string map {& &amp; < &lt; > &gt; \" &quot;} $s }

proc leaf_attrs {idx name} {
    global TPAT
    set a "data_index=\"$idx\" duplicate_name_allowed=\"false\" is_data_input=\"true\" is_node_valid=\"true\" is_selected=\"false\" is_storage_input=\"false\" is_trigger_input=\"true\""
    if {[info exists TPAT($name)]} {
        append a " level-0=\"$TPAT($name)\""
    } else {
        append a " level-0=\"dont_care\""
    }
    append a " name=\"[xesc $name]\" tap_mode=\"classic\" trigger_index=\"$idx\" type=\"unknown\""
    return $a
}

set f [open $out w]
puts $f "<session jtag_chain=\"[xesc $JTAG_CHAIN]\" jtag_device=\"[xesc $JTAG_DEVICE]\" sof_file=\"\">"
puts $f "  <display_tree gui_logging_enabled=\"0\">"
puts $f "    <display_branch instance=\"$INSTANCE_NAME\" log=\"USE_GLOBAL_TEMP\" signal_set=\"USE_GLOBAL_TEMP\" trigger=\"USE_GLOBAL_TEMP\"/>"
puts $f "  </display_tree>"
puts $f "  <instance enabled=\"true\" entity_name=\"sld_signaltap\" is_auto_node=\"yes\" is_expanded=\"true\" name=\"$INSTANCE_NAME\" source_file=\"sld_signaltap.vhd\">"
puts $f "    <node_ip_info instance_id=\"0\" mfg_id=\"110\" node_id=\"0\" version=\"6\"/>"
puts $f "    <signal_set global_temp=\"1\" is_expanded=\"true\" name=\"$SS_NAME\">"
puts $f "      <clock name=\"[xesc $CLOCK_NODE]\" polarity=\"posedge\" tap_mode=\"classic\"/>"
puts $f "      <config pipeline_level=\"0\" ram_type=\"AUTO\" reserved_data_nodes=\"0\" reserved_storage_qualifier_nodes=\"0\" reserved_trigger_nodes=\"0\" sample_depth=\"$SAMPLE_DEPTH\" trigger_in_enable=\"no\" trigger_out_enable=\"no\"/>"
puts $f "      <top_entity/>"
puts $f "      <signal_vec>"
puts $f "        <trigger_input_vec>"
foreach b $BITS { puts $f "          <wire name=\"[xesc $b]\" tap_mode=\"classic\"/>" }
puts $f "        </trigger_input_vec>"
puts $f "        <data_input_vec>"
foreach b $BITS { puts $f "          <wire name=\"[xesc $b]\" tap_mode=\"classic\"/>" }
puts $f "        </data_input_vec>"
puts $f "        <storage_qualifier_input_vec/>"
puts $f "      </signal_vec>"
puts $f "      <presentation>"
puts $f "        <unified_setup_data_view>"
set i 0
foreach b $BITS { puts $f "          <node [leaf_attrs $i $b]/>" ; incr i }
puts $f "        </unified_setup_data_view>"
puts $f "        <data_view>"
set i 0
foreach b $BITS { puts $f "          <net [leaf_attrs $i $b]/>" ; incr i }
puts $f "        </data_view>"
puts $f "        <setup_view>"
set i 0
foreach b $BITS { puts $f "          <net [leaf_attrs $i $b]/>" ; incr i }
puts $f "        </setup_view>"
puts $f "        <trigger_in_editor/>"
puts $f "        <trigger_out_editor/>"
puts $f "      </presentation>"
puts $f "      <trigger CRC=\"DDDA0001\" attribute_mem_mode=\"false\" gap_record=\"true\" global_temp=\"1\" is_expanded=\"true\" name=\"$TRIG_NAME\" position=\"$TRIGGER_POSITION\" power_up_trigger_mode=\"false\" record_data_gap=\"true\" segment_size=\"1\" storage_mode=\"$STORAGE_MODE\" storage_qualifier_disabled=\"yes\" storage_qualifier_port_is_pin=\"false\" storage_qualifier_port_name=\"auto_stp_external_storage_qualifier\" storage_qualifier_port_tap_mode=\"classic\" trigger_type=\"circular\">"
puts $f "        <power_up_trigger position=\"$TRIGGER_POSITION\" storage_qualifier_disabled=\"yes\"/>"
puts $f "        <events use_custom_flow_control=\"no\">"
set terms {}
foreach t $TRIGGER_TERMS { lassign $t tn tp ; lappend terms "'[xesc $tn]' == $tp" }
puts $f "          <level enabled=\"yes\" name=\"condition1\" type=\"basic\">[join $terms { &amp;&amp; }]"
puts $f "            <power_up enabled=\"yes\">"
puts $f "            </power_up><op_node/>"
puts $f "          </level>"
puts $f "        </events>"
puts $f "        <storage_qualifier_events>"
puts $f "          <transitional>1"
puts $f "            <pwr_up_transitional>1</pwr_up_transitional>"
puts $f "          </transitional>"
puts $f "          <storage_qualifier_level type=\"basic\">"
puts $f "            <power_up>"
puts $f "            </power_up>"
puts $f "            <op_node/>"
puts $f "          </storage_qualifier_level>"
puts $f "        </storage_qualifier_events>"
puts $f "        <log>"
puts $f "          <data global_temp=\"1\" name=\"log: empty\"/>"
puts $f "          <extradata/>"
puts $f "        </log>"
puts $f "      </trigger>"
puts $f "    </signal_set>"
puts $f "    <position_info>"
puts $f "      <single attribute=\"active tab\" value=\"1\"/>"
puts $f "    </position_info>"
puts $f "  </instance>"
puts $f "  <mnemonics/>"
puts $f "  <static_plugin_mnemonics/>"
puts $f "  <global_info>"
puts $f "    <single attribute=\"active instance\" value=\"0\"/>"
puts $f "  </global_info>"
puts $f "</session>"
close $f

puts "wrote $out: $NBITS data bits, depth $SAMPLE_DEPTH, clock=$CLOCK_NODE"
puts "trigger=$TRIG_NAME ([llength $TRIGGER_TERMS] term), storage=$STORAGE_MODE"
puts "NEXT: validate with open_session in the dell docker (RUNBOOK step 0)"
