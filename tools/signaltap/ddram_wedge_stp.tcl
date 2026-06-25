#!/usr/bin/env tclsh
# =============================================================================
# ddram_wedge_stp.tcl -- GENERATE the SignalTap .stp for the dvd MPEG-2 core's
# f2sdram throttle-hold WEDGE probe.
#
# QUESTION (the one capture this answers): when the mem_shim read-throttle HOLDS
# (no command issued while reads are outstanding), do read RESPONSES keep
# arriving (rdv_q keeps pulsing -> the wedge is something else) or STOP
# (confirms the command-gap -> bridge-stops-draining mechanism inferred from 3
# HW builds + the sim oracle)? And the write-side wedge: does ram_write stay
# asserted in S_WAIT (state=1) for a long run = the bridge stuck refusing it
# (BUSY stuck)?
#
# PROBE = EXISTING mem_shim registers ONLY (per the probe-wedge lesson: tapping
# existing regs from our module, NOT adding bridge-adjacent observer logic and
# NOT pin-tapping, minimizes placement re-roll). rdv_q (Option A) IS the
# registered DDRAM_DOUT_READY, so it shows the read-response pulses directly.
#
# Hierarchy (verified from emu.sv + the cap6 fit.rpt, 2026-06-25):
#   sys_top (top) -> emu:emu (rtl/emu.sv) -> mem_shim:mem_shim_inst (emu.sv:425)
# Capture clock = clk_mem (108 MHz, sys_pll outclk_1). The PLL OUTPUT NET (gate
# #4: never a wire alias) resolved in the fit report as outclk_wire[1].
#
# USAGE:   tclsh ddram_wedge_stp.tcl [out.stp]   (default ddram_wedge.stp here)
# VALIDATE (seconds, no build): scp to dell, open_session in raetro/quartus:17.0
#   -> expect OPEN-OK (RUNBOOK step 0). Shape-check only; real gate is the build.
# RE-TUNE WITHOUT REBUILD: every tapped node is a trigger input, so the trigger
#   pattern can be changed by regenerating + re-capturing (no rebuild) as long as
#   the NODE LIST, depth, and clock are unchanged.
# =============================================================================

# --- hierarchy -------------------------------------------------------------
set MS "emu:emu|mem_shim:mem_shim_inst"

# Capture clock: clk_mem = sys_pll outclk_1 (108 MHz). PLL output net.
set CLOCK_NODE {emu:emu|sys_pll:sys_pll|altera_pll:altera_pll_i|outclk_wire[1]}

# JTAG identity (overridden at capture time by capture_headless after live
# jtagconfig discovery; only needs to be plausible for the .stp to open).
set JTAG_CHAIN  "DE-SoC \[1-4\]"
set JTAG_DEVICE "@2: 5CSEBA6(.|ES)/5CSEMA6/.. (0x02D020DD)"

set INSTANCE_NAME "auto_signaltap_0"
set SS_NAME       "ss_ddram_wedge"
set TRIG_NAME     "trig_ddram_wedge"

# 12-bit probe; depth 8192 (~76 us @108MHz) = a wide PRE-trigger window so the
# whole lead-in (read burst -> throttle hold -> gap -> wedge) is captured.
set SAMPLE_DEPTH 8192
set TRIGGER_POSITION "post"   ;# ~7/8 of the buffer PRECEDES the trigger row

# Continuous storage (store every clk_mem cycle) -- we MUST see whether rdv_q
# pulses during the command gap, so no storage qualifier.
set STORAGE_MODE "continuous"

# --- watch list  {name width}  (width>1 expands name[0..width-1]) ----------
set NODES [list \
    [list "$MS|ram_read"           1] \
    [list "$MS|ram_write"          1] \
    [list "$MS|rdv_q"              1] \
    [list "$MS|state"              1] \
    [list "$MS|saved_valid"        1] \
    [list "$MS|wedged"             1] \
    [list "$MS|outstanding_reads"  6] \
]
# 1+1+1+1+1+1+6 = 12 bits.

# --- trigger: fire when the wedge latches (mem_shim wedged flag) ------------
set TRIGGER_TERMS [list [list "$MS|wedged" high]]

# ===========================================================================
# generation -- no user-serviceable parts below (XML schema mirrored from the
# 573 silicon-proven generator; continuous-storage variant).
# ===========================================================================
set out "ddram_wedge.stp"
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

# one leaf line per bit; continuous storage => no storage-input role
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
# CRC must be NONZERO (gate #5: a zero CRC makes the runtime refuse to arm).
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
