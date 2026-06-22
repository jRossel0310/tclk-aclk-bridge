# vivado/build_clk.tcl - unified ACLK/TCLK readout on the KR260.
#
# PS (pl_clk0 100 AXI + a PL MMCM 80/40 MHz) + the unified readout (clk_readout_bd_top)
# on the LPD AXI master at 0x8000_0000. The serial line enters on H12. Decodes BOTH real
# TCLK and real ACLK-Lite (serdec4_9MHz + clk_byte_framer). Reuses design_name=uart_echo_bd
# so the bitstream name and the overlay load unchanged. Counterpart to build_tclk.tcl,
# which is left untouched.
#
# Build:  .\hw.ps1 build -Tcl vivado\build_clk.tcl -Name clk

set proj_name   clk
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_clk.xdc]

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

# RTL sources: shared readout chain + serdec + unified framer/decoder/top + BD top.
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
    [file join $rtl_dir aclk_bridge serdec4_9MHz.v] \
    [file join $rtl_dir aclk_lite clk_byte_framer.sv] \
    [file join $rtl_dir aclk_lite clk_rcv.sv] \
    [file join $rtl_dir aclk_lite clk_readout_top.sv] \
    [file join $rtl_dir clk_readout_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

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

set clk [create_bd_cell -type module -reference clk_readout_bd_top u_clk]

# PS -> our slave over the LPD master via an AXI SmartConnect (the auto interconnect
# dropped read data for non-16-byte-aligned offsets on hardware). One clock domain.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]

set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins axi_sc/aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]

connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins u_clk/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_clk/s_axi_aresetn]

connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] [get_bd_intf_pins axi_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins u_clk/S_AXI]

# Clocking Wizard: 80 + 40 MHz from pl_clk0 INSIDE the PL (depend only on pl_clk0). The
# MMCM input takes pl_clk0 with no buffer; feed pl_clk0's EXACT realized Hz as PRIM_IN_FREQ
# or validate_bd_design trips BD 41-238.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {80.000} \
    CONFIG.CLKOUT2_USED {true} \
    CONFIG.CLKOUT2_REQUESTED_OUT_FREQ {40.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_clk/clk_80m]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins u_clk/clk_40m]
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins u_clk/mmcm_locked]

# Reset: tie the auto proc_sys_reset's dcm_locked HIGH (proven topology). Gating
# s_axi_aresetn on clk_wiz/locked WEDGED the LPD bus on hardware.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]
connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins u_clk/rstn]

# External serial line -> H12 (constrained in the XDC).
create_bd_port -dir I clkline
connect_bd_net [get_bd_port clkline] [get_bd_pins u_clk/clkline]

# Clock-alive scope diagnostics out to Pmod pins.
create_bd_port -dir O clk40_dbg
create_bd_port -dir O clk100_dbg
create_bd_port -dir O cdc_dbg
create_bd_port -dir O dbg_hb
connect_bd_net [get_bd_port clk40_dbg]  [get_bd_pins u_clk/clk40_dbg]
connect_bd_net [get_bd_port clk100_dbg] [get_bd_pins u_clk/clk100_dbg]
connect_bd_net [get_bd_port cdc_dbg]    [get_bd_pins u_clk/cdc_dbg]
connect_bd_net [get_bd_port dbg_hb]     [get_bd_pins u_clk/dbg_hb]

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
