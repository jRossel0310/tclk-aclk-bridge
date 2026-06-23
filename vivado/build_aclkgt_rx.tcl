# vivado/build_aclkgt_rx.tcl - Milestone 1: RECEIVER (real SFP RX, no loopback).
#
# One KR260 board receiving a real gigabit-ACLK 8b10b stream off the SFP+ cage.
# The GT (aclkgt_gt, GTH, 1.25 Gbps, 8b10b, QPLL0, refclk 156.25 MHz) runs in normal
# mode (loopback_in=000); aclk_gt_readout_top decodes RX into events the PS reads over
# AXI-Lite. No on-board generator. Pair with build_aclkgt_gen.tcl on a second board.
# design_name=uart_echo_bd so the overlay loads unchanged.
#
# Build:  .\hw.ps1 build -Tcl vivado\build_aclkgt_rx.tcl -Name aclkgt_rx

set proj_name   aclkgt_rx
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_aclkgt_rx.xdc]

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

# ---- GT IP ----
set gt_xci [file join $root_dir vivado ip aclkgt_gt aclkgt_gt.xci]
read_ip $gt_xci
generate_target all [get_ips aclkgt_gt]

# ---- RTL sources (RX decode path only; no generator/96->16 gearbox) ----
add_files -norecurse [list \
    [file join $rtl_dir aclk_bridge crc8_calc.v] \
    [file join $rtl_dir aclk_bridge GEARBOX_16_TO_96.v] \
    [file join $rtl_dir aclk_bridge ACLK_REV.v] \
]
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
]
add_files -norecurse [list \
    [file join $rtl_dir aclk_gt aclk_gt_readout_top.sv] \
    [file join $rtl_dir aclk_gt_rx_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

# ---- Block design ----
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

set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]

set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins axi_sc/aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk]

set u_aclkgt [create_bd_cell -type module -reference aclk_gt_rx_bd_top u_aclkgt]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins u_aclkgt/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_aclkgt/s_axi_aresetn]
connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] [get_bd_intf_pins axi_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins u_aclkgt/S_AXI]

set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set_property -dict [list \
    CONFIG.PRIM_SOURCE              {No_buffer} \
    CONFIG.PRIM_IN_FREQ             $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.USE_LOCKED               {true} \
    CONFIG.USE_RESET               {true} \
    CONFIG.RESET_TYPE               {ACTIVE_LOW} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_aclkgt/freerun_50]

set rst_cell [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst_cell/dcm_locked]
connect_bd_net [get_bd_pins $rst_cell/peripheral_aresetn] [get_bd_pins u_aclkgt/rstn]

create_bd_port -dir O dbg_hb
connect_bd_net [get_bd_port dbg_hb] [get_bd_pins u_aclkgt/dbg_hb]

# External GT refclk + SFP serial (RX in, TX idle out)
create_bd_port -dir I gt_refclk_p
create_bd_port -dir I gt_refclk_n
create_bd_port -dir I gt_rxp
create_bd_port -dir I gt_rxn
create_bd_port -dir O gt_txp
create_bd_port -dir O gt_txn
connect_bd_net [get_bd_port gt_refclk_p] [get_bd_pins u_aclkgt/gt_refclk_p]
connect_bd_net [get_bd_port gt_refclk_n] [get_bd_pins u_aclkgt/gt_refclk_n]
connect_bd_net [get_bd_port gt_rxp]      [get_bd_pins u_aclkgt/gt_rxp]
connect_bd_net [get_bd_port gt_rxn]      [get_bd_pins u_aclkgt/gt_rxn]
connect_bd_net [get_bd_port gt_txp]      [get_bd_pins u_aclkgt/gt_txp]
connect_bd_net [get_bd_port gt_txn]      [get_bd_pins u_aclkgt/gt_txn]

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
