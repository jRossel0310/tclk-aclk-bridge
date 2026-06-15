# vivado/build_tclk.tcl — real-TCLK readout on the KR260.
#
# PS (three PL clocks: 100 AXI, 80 serdec, 40 deserializer/readout) + the TCLK
# readout (tclk_readout_bd_top) on the LPD AXI master at 0x8000_0000. The TCLK line
# enters on H12. Reuses design_name=uart_echo_bd so the bitstream is named
# uart_echo_bd_wrapper.bit.bin and the existing overlay loads unchanged.
#
# Build:  vivado -mode batch -source vivado/build_tclk.tcl
#    or:  .\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk   (PS, with AV retry)

set proj_name   tclk
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_tclk.xdc]

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

# RTL sources: readout chain + TCLK decoder + tclk_readout_top + the Verilog BD top.
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
    [file join $rtl_dir aclk_bridge serdec4_9MHz.v] \
    [file join $rtl_dir aclk_bridge TCLK_DESERIALIZER2.v] \
    [file join $rtl_dir aclk_bridge TCLK_RCV.v] \
    [file join $rtl_dir aclk_lite tclk_readout_top.sv] \
    [file join $rtl_dir tclk_readout_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

# Zynq US+ PS with board preset; pl_clk0 (100 MHz) + the LPD AXI master. The 80/40
# MHz receive clocks are made in the PL by a Clocking Wizard below, NOT by the PS:
# a runtime bitstream load cannot reprogram the PS PL clock frequencies (pl_clk1/2
# stay at boot defaults), so the design must depend only on pl_clk0.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
] $ps

# Our TCLK readout as a module reference (the Verilog BD wrapper).
set tclk [create_bd_cell -type module -reference tclk_readout_bd_top u_tclk]

# PS -> our AXI slave over LPD; automation builds interconnect + reset + clock +
# address, and connects s_axi_aclk/s_axi_aresetn via the inferred S_AXI interface.
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config { \
    Master      {/zynq_ultra_ps_e_0/M_AXI_HPM0_LPD} \
    Clk_xbar    {Auto} \
    Clk_master  {Auto} \
    Clk_slave   {Auto} \
    intc_ip     {Auto} \
    master_apm  {0} \
} [get_bd_intf_pins u_tclk/S_AXI]

# Clocking Wizard: make the 80 + 40 MHz receive clocks from pl_clk0 (100 MHz)
# INSIDE the PL, so we depend only on pl_clk0 (the one PL clock a runtime bitstream
# load leaves at the expected frequency). pl_clk0 is an internal clock net, so the
# MMCM input takes it with no buffer. The MMCM 'locked' drives the reset.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ {100.000} \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {80.000} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {40.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {false} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_tclk/clk_80m]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins u_tclk/clk_40m]

# Reset: hold the design in reset until the MMCM locks (dcm_locked = clk_wiz locked).
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins $rst/dcm_locked]
connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins u_tclk/rstn]

# External TCLK input -> H12 (constrained in the XDC).
create_bd_port -dir I tclk
connect_bd_net [get_bd_port tclk] [get_bd_pins u_tclk/tclk]

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
    puts "ERROR: bitstream not found — check the impl_1 run log."
    exit 1
}
