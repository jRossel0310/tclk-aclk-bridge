# vivado/build_aclk_pipeline.tcl - INTEGRATED TCLK -> ACLK pipeline (one board).
#
# Builds the full single-board pipeline (rtl/aclk_pipeline_bd_top.v):
#   tclk (H12, biphase-mark) -> TCLK readout (AXI S_AXI @ 0x8000_0000)
#     -> aclk_tclk_encoder -> GT TX -> SFP (external fiber loop) -> GT RX
#     -> ACLK readout (AXI S_AXI2 @ 0x8001_0000)
#     -> aclk_lite_bridge/encoder -> ACLK-Lite biphase-mark out (B10).
#
# The block design is modelled on build_aclkgt_selftest.tcl (GT IP, freerun clk_wiz,
# proc_sys_reset + dcm_locked tied high, SFP sideband ports) PLUS the build_tclk.tcl
# 80/40 MHz clk_wiz for the TCLK / ACLK-Lite event domains. The custom top is added as
# a module-ref cell that Vivado infers TWO AXI4-Lite slaves from (S_AXI, S_AXI2); a
# SmartConnect (NUM_MI=2) fans the LPD master out to both.
#
# design_name=uart_echo_bd so the overlay/UIO identity is unchanged: the bitstream is
# named uart_echo_bd_wrapper.bit.bin and the existing overlay loads as-is.
#
# Build:  .\hw.ps1 build -Tcl vivado\build_aclk_pipeline.tcl -Name aclk_pipeline
#   (hw.ps1 runs synth/impl/write_bitstream here, then packages bootgen -> .bit.bin)

set proj_name   aclk_pipeline
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_aclk_pipeline.xdc]

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

# ---- GT IP (aclkgt_gt: GTH, 1.25 Gbps, 8b10b, refclk 156.25 MHz) ----
# Use the committed .xci exactly as build_aclkgt_selftest.tcl does (the IP was generated
# + netlist-verified by vivado/ip/gen_aclkgt_gt.tcl; do not regenerate here).
set gt_xci [file join $root_dir vivado ip aclkgt_gt aclkgt_gt.xci]
read_ip $gt_xci
generate_target all [get_ips aclkgt_gt]

# ---- RTL sources ----
# GT decode/encode primitives (ACLK_RCV decode path + the TCLK->ACLK encoder gearbox).
add_files -norecurse [list \
    [file join $rtl_dir aclk_bridge crc8_calc.v] \
    [file join $rtl_dir aclk_bridge GEARBOX_16_TO_96.v] \
    [file join $rtl_dir aclk_bridge ACLK_REV.v] \
    [file join $rtl_dir aclk_bridge gearbox_96_to_16.v] \
]
# TCLK decode path (serdec -> deserializer -> TCLK_RCV).
add_files -norecurse [list \
    [file join $rtl_dir aclk_bridge serdec4_9MHz.v] \
    [file join $rtl_dir aclk_bridge TCLK_DESERIALIZER2.v] \
    [file join $rtl_dir aclk_bridge TCLK_RCV.v] \
]
# Common readout + CDC primitives (both readouts use the async-FIFO core + AXI slave).
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
]
# Pipeline glue: the two readout tops, the TCLK->ACLK encoder, the shared timebase,
# the ACLK-Lite bridge + encoder, and the integrated BD top.
add_files -norecurse [list \
    [file join $rtl_dir aclk_lite tclk_readout_top.sv] \
    [file join $rtl_dir aclk_gt aclk_gt_readout_top.sv] \
    [file join $rtl_dir aclk_gt aclk_tclk_encoder.v] \
    [file join $rtl_dir global_timebase.v] \
    [file join $rtl_dir aclk_lite_bridge.v] \
    [file join $rtl_dir aclk_lite aclk_lite_encoder.sv] \
    [file join $rtl_dir aclk_pipeline_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

# ---- Block design ----
create_bd_design $design_name

# Zynq US+ PS: board preset, pl_clk0 = 100 MHz, LPD AXI master (M_AXI_HPM0_LPD).
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

# proc_sys_reset on pl_clk0 (its dcm_locked is tied HIGH below: the proven LPD workaround).
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]

# Drive the PS LPD master interface clock from pl_clk0.
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk]

# ---- The integrated pipeline top as a module-ref cell ----
# Vivado infers TWO AXI4-Lite slave interfaces (S_AXI, S_AXI2) from the X_INTERFACE
# attributes in aclk_pipeline_bd_top.v. Both share s_axi_aclk / s_axi_aresetn (pl_clk0).
set u [create_bd_cell -type module -reference aclk_pipeline_bd_top u_pipeline]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins u_pipeline/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_pipeline/s_axi_aresetn]

# ---- SmartConnect: LPD master -> two AXI4-Lite slaves (NUM_SI=1, NUM_MI=2) ----
# A single-clock SmartConnect (everything on pl_clk0) cleanly fans the LPD master out
# to both readout slaves. (The auto interconnect+protocol_converter path corrupts
# AXI4->AXI4-Lite read data on hardware; SmartConnect is the proven fix.)
set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {2}] $sc
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins axi_sc/aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]
connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] [get_bd_intf_pins axi_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins u_pipeline/S_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M01_AXI] [get_bd_intf_pins u_pipeline/S_AXI2]

# ---- clk_wiz #1: 80 + 40 MHz event-domain clocks from pl_clk0 (from build_tclk.tcl) ----
# pl_clk0's realized rate is not exactly 100 MHz; clk_wiz makes clk_in1's FREQ_HZ
# read-only and derives it from PRIM_IN_FREQ, so feed pl_clk0's EXACT Hz (6 decimals of
# MHz) as PRIM_IN_FREQ or validate_bd_design trips BD 41-238. resetn from pl_resetn0 so
# the MMCM gets a real reset and re-locks after a runtime fpgautil PL reconfigure.
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
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
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]   [get_bd_pins clk_wiz_0/clk_in1]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_pipeline/clk_80m]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins u_pipeline/clk_40m]

# ---- clk_wiz #2: 50 MHz free-running clock for the GT reset controller (from selftest) ----
set clkf [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_freerun]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {50.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clkf
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]   [get_bd_pins clk_wiz_freerun/clk_in1]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_freerun/resetn]
connect_bd_net [get_bd_pins clk_wiz_freerun/clk_out1] [get_bd_pins u_pipeline/freerun_50]

# ---- Reset: tie the proc_sys_reset's dcm_locked HIGH; drive u_pipeline/rstn from
# peripheral_aresetn (the topology proven on the working tclk / selftest builds). ----
set rst_cell [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst_cell/dcm_locked]
connect_bd_net [get_bd_pins $rst_cell/peripheral_aresetn] [get_bd_pins u_pipeline/rstn]

# ---- External BD ports ----
# TCLK input (H12) + the ACLK-Lite mirror output + the debug heartbeat.
create_bd_port -dir I tclk
create_bd_port -dir O aclk_lite_out
create_bd_port -dir O dbg_hb
connect_bd_net [get_bd_port tclk]          [get_bd_pins u_pipeline/tclk]
connect_bd_net [get_bd_port aclk_lite_out] [get_bd_pins u_pipeline/aclk_lite_out]
connect_bd_net [get_bd_port dbg_hb]        [get_bd_pins u_pipeline/dbg_hb]

# SFP+ sideband control/status (drive TX_DISABLE low to enable the laser; monitor the rest).
create_bd_port -dir O sfp_tx_disable
create_bd_port -dir I sfp_tx_fault
create_bd_port -dir I sfp_rx_los
create_bd_port -dir I sfp_mod_abs
connect_bd_net [get_bd_port sfp_tx_disable] [get_bd_pins u_pipeline/sfp_tx_disable]
connect_bd_net [get_bd_port sfp_tx_fault]   [get_bd_pins u_pipeline/sfp_tx_fault]
connect_bd_net [get_bd_port sfp_rx_los]     [get_bd_pins u_pipeline/sfp_rx_los]
connect_bd_net [get_bd_port sfp_mod_abs]    [get_bd_pins u_pipeline/sfp_mod_abs]

# External GT refclk + SFP serial (RX = data in; TX = re-encoded ACLK out).
create_bd_port -dir I gt_refclk_p
create_bd_port -dir I gt_refclk_n
create_bd_port -dir I gt_rxp
create_bd_port -dir I gt_rxn
create_bd_port -dir O gt_txp
create_bd_port -dir O gt_txn
connect_bd_net [get_bd_port gt_refclk_p] [get_bd_pins u_pipeline/gt_refclk_p]
connect_bd_net [get_bd_port gt_refclk_n] [get_bd_pins u_pipeline/gt_refclk_n]
connect_bd_net [get_bd_port gt_rxp]      [get_bd_pins u_pipeline/gt_rxp]
connect_bd_net [get_bd_port gt_rxn]      [get_bd_pins u_pipeline/gt_rxn]
connect_bd_net [get_bd_port gt_txp]      [get_bd_pins u_pipeline/gt_txp]
connect_bd_net [get_bd_port gt_txn]      [get_bd_pins u_pipeline/gt_txn]

# ---- Address map: the two AXI4-Lite slaves at 0x8000_0000 / 0x8001_0000 ----
# A module-reference AXI slave carries no IP-XACT memory map, so its Reg address
# segment does NOT exist until assign_bd_address auto-creates it (the proven single-
# slave builds just call bare `assign_bd_address`). So: bare-assign first to create
# both segments, then relocate each to its base, referencing the segment by
# -of_objects (the auto-generated segment name is not u_pipeline/S_AXI/Reg).
assign_bd_address
assign_bd_address -offset 0x80000000 -range 0x10000 -force -target_address_space \
    [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] \
    [get_bd_addr_segs -of_objects [get_bd_intf_pins u_pipeline/S_AXI]]
assign_bd_address -offset 0x80010000 -range 0x10000 -force -target_address_space \
    [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] \
    [get_bd_addr_segs -of_objects [get_bd_intf_pins u_pipeline/S_AXI2]]

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
