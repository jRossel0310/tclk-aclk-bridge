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

# PS -> our AXI slave over the LPD master, via an AXI SmartConnect -- NOT the
# apply_bd_automation interconnect+protocol_converter. On hardware that auto path only
# returned read data for 16-byte-aligned offsets: a single 32-bit read of a non-aligned
# register DID reach the slave (the slave latched the right araddr and drove the right
# rdata), but the interconnect/protocol-converter dropped the read data on the way back
# -- an AXI4->AXI4-Lite read-data byte-lane bug. SmartConnect handles that path cleanly.
# The PS LPD master, the SmartConnect, and our slave are all on pl_clk0 (one clock
# domain), so a single-clock SmartConnect is all we need.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]

set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins axi_sc/aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]

# Drive the PS LPD master interface clock + our slave clock from pl_clk0; reset both
# from the proc_sys_reset's peripheral_aresetn (s_axi_aresetn and the rx-side rstn,
# the latter wired below with dcm_locked tied high).
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins u_tclk/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_tclk/s_axi_aresetn]

connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] [get_bd_intf_pins axi_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins u_tclk/S_AXI]

# Clocking Wizard: make the 80 + 40 MHz receive clocks from pl_clk0 (100 MHz)
# INSIDE the PL, so we depend only on pl_clk0 (the one PL clock a runtime bitstream
# load leaves at the expected frequency). pl_clk0 is an internal clock net, so the
# MMCM input takes it with no buffer. The MMCM 'locked' drives the reset.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
# pl_clk0's realized rate is not exactly 100 MHz: the PS PLL lands on an odd value
# (e.g. 99999001 Hz). clk_wiz makes clk_in1's FREQ_HZ read-only and derives it from
# PRIM_IN_FREQ, so the ONLY way to match pl_clk0 to the Hz (or validate_bd_design
# trips BD 41-238) is to feed pl_clk0's exact rate in as PRIM_IN_FREQ. clk_wiz keeps
# 6 decimals of MHz, so 99999001 Hz -> 99.999001 MHz -> 99999001 Hz on clk_in1.
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
# Give the MMCM a real reset (it was USE_RESET=false). On a runtime fpgautil load the
# PL is reconfigured while pl_clk0 is already toggling; if the MMCM misses lock during
# that window and has no reset it can NEVER re-acquire -> clk_40m/clk_80m stay dead
# (observed on hardware: heartbeat 0x2C frozen, EVENT_COUNT=0, tclk_edges=0, yet AXI
# works because pl_clk0 is alive). pl_resetn0 is held asserted through config and the
# overlay releases it once pl_clk0 is stable, so the MMCM resets and locks cleanly.
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_tclk/clk_80m]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out2] [get_bd_pins u_tclk/clk_40m]
# Expose the MMCM locked status to the PS at AXI 0x30 (s_axi_aclk domain, always alive)
# so a board read tells "MMCM never locked" (0x30=0) apart from "locked but rx clock
# dead" (0x30=1). The dcm_locked reset gating uses the xlconstant above, not this.
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins u_tclk/mmcm_locked]

# Reset: tie the auto proc_sys_reset's dcm_locked HIGH (the topology proven on the
# working pltest/uart_echo builds). s_axi_aresetn comes from this proc_sys_reset;
# gating it on clk_wiz/locked WEDGED the LPD bus on hardware -- the reset stayed
# asserted (the MMCM lock never released it), so the AXI-Lite slave held ARREADY=1
# with RVALID=0 and every read hung the CPU forever. Tying dcm_locked high releases
# peripheral_aresetn as soon as pl_resetn deasserts, independent of the MMCM, so the
# slave (clocked by the always-on pl_clk0) responds. The rx logic comes out of reset
# too; the MMCM locks within microseconds and the decoder re-locks (the startup PERR
# is already handled), so brief clk_40m/clk_80m instability at boot is harmless.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]
connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins u_tclk/rstn]

# External TCLK input -> H12 (constrained in the XDC).
create_bd_port -dir I tclk
connect_bd_net [get_bd_port tclk] [get_bd_pins u_tclk/tclk]

# Clock-alive scope diagnostics: clk_40m/1024 and pl_clk0/1024 out to Pmod pins so a
# scope can confirm (independent of the AXI readout) whether the MMCM clock toggles.
create_bd_port -dir O clk40_dbg
create_bd_port -dir O clk100_dbg
create_bd_port -dir O cdc_dbg
create_bd_port -dir O dbg_hb
connect_bd_net [get_bd_port clk40_dbg]  [get_bd_pins u_tclk/clk40_dbg]
connect_bd_net [get_bd_port clk100_dbg] [get_bd_pins u_tclk/clk100_dbg]
connect_bd_net [get_bd_port cdc_dbg]    [get_bd_pins u_tclk/cdc_dbg]
connect_bd_net [get_bd_port dbg_hb]     [get_bd_pins u_tclk/dbg_hb]

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
