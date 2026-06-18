# vivado/build_aclkgen.tcl - ACLK-Lite signal GENERATOR on the KR260.
#
# Counterpart to build_aclk.tcl (the receiver), but this is the TX/generator. It
# reuses the receiver's proven clocking topology verbatim - PS pl_clk0 (100 MHz) +
# a PL clk_wiz MMCM deriving a 120 MHz oversample clock (clk_os), MMCM resetn from
# pl_resetn0, an auto proc_sys_reset with dcm_locked tied HIGH - but has NO AXI: the
# generator boots and transmits a hardcoded timeline, so there is no LPD AXI master,
# no SmartConnect, and no AXI slave. The Manchester stream leaves on H12 (constrained
# in the XDC). Reuses design_name=uart_echo_bd so the bitstream is named
# uart_echo_bd_wrapper.bit.bin and the existing overlay loads unchanged.
# build_aclk.tcl and all receiver/TCLK files are left untouched.
#
# Build:  vivado -mode batch -source vivado/build_aclkgen.tcl
#    or:  .\hw.ps1 build -Tcl vivado\build_aclkgen.tcl -Name aclkgen

set proj_name   aclkgen
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_aclkgen.xdc]

if {[info exists ::env(KRIA_BUILD_DIR)] && [string length $::env(KRIA_BUILD_DIR)] > 0} {
    set build_dir $::env(KRIA_BUILD_DIR)
} elseif {[info exists ::env(USERPROFILE)]} {
    set build_dir [file join $::env(USERPROFILE) kria-builds $proj_name]
} elseif {[info exists ::env(HOME)]} {
    set build_dir [file join $::env(HOME) kria-builds $proj_name]
} else {
    set build_dir [file join $script_dir build_$proj_name]
}
puts "INFO: building in $build_dir"

create_project -force $proj_name $build_dir -part $part
set bp [get_board_parts -quiet -filter {NAME =~ "*kr260*"}]
if {[llength $bp] > 0} { set_property board_part [lindex $bp 0] [current_project] }

# RTL sources: the encoder + hardcoded timeline + the no-AXI BD wrapper. The generator
# instantiates only the encoder/timeline (no synchronizer/decoder dependency).
add_files -norecurse [list \
    [file join $rtl_dir aclk_lite aclk_lite_encoder.sv] \
    [file join $rtl_dir aclk_lite aclk_lite_gen_timeline.sv] \
    [file join $rtl_dir aclk_gen_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

# Zynq US+ PS with board preset; pl_clk0 (100 MHz). No AXI masters (generator only).
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {0} \
] $ps

# Our generator as a module reference (the Verilog BD wrapper).
set gen [create_bd_cell -type module -reference aclk_gen_bd_top u_gen]

# Reset: a proc_sys_reset off pl_clk0 / pl_resetn0, dcm_locked tied HIGH (proven on
# the tclk/uart_echo/receiver builds; gating reset on MMCM lock wedged the design on
# hardware). Its peripheral_aresetn is the design rstn.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins rst_pl0/dcm_locked]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_gen/rstn]

# Clocking Wizard: ONE 120 MHz oversample clock (clk_os) from pl_clk0 INSIDE the PL,
# so we depend only on pl_clk0 (the one PL clock a runtime bitstream load leaves at
# the expected frequency). pl_clk0 is an internal net, so the MMCM input takes it with
# no buffer. clk_wiz's clk_in1 FREQ_HZ is read-only and derived from PRIM_IN_FREQ, so
# feed pl_clk0's exact realized rate in or validate_bd_design trips BD 41-238.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {120.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
# Give the MMCM a real reset from pl_resetn0 so it re-locks after a runtime fpgautil
# load (the PL is reconfigured while pl_clk0 already toggles; without a reset a missed
# lock can never re-acquire -> clk_os stays dead).
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_gen/clk_os]
# clk_wiz/locked is intentionally left unconnected (the design does not gate on it;
# the MMCM still locks within microseconds, same as the receiver build).

# External Manchester output -> H12, plus the two scope-debug pins (constrained in XDC).
create_bd_port -dir O aclk_out
create_bd_port -dir O frame_sync_dbg
create_bd_port -dir O clkos_dbg
connect_bd_net [get_bd_port aclk_out]       [get_bd_pins u_gen/aclk_out]
connect_bd_net [get_bd_port frame_sync_dbg] [get_bd_pins u_gen/frame_sync_dbg]
connect_bd_net [get_bd_port clkos_dbg]      [get_bd_pins u_gen/clkos_dbg]

assign_bd_address
regenerate_bd_layout
validate_bd_design
save_bd_design

set bd_file [get_files ${design_name}.bd]
make_wrapper -files $bd_file -top -import
set_property top ${design_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

set bit [file join $build_dir ${proj_name}.runs impl_1 ${design_name}_wrapper.bit]
if {[file exists $bit]} {
    puts "=========================================================="
    puts "BITSTREAM: $bit"
    puts "=========================================================="
} else {
    puts "ERROR: bitstream not found - check the impl_1 run log."
    exit 1
}
