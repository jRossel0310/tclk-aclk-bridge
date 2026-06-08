# vivado/build_pltest.tcl — minimal "is the PL running?" design for the KR260.
#
# A free-running 32-bit counter (pl_heartbeat) feeds an AXI GPIO (all inputs), so
# the PS can read the counter over AXI (LPD, base 0x8000_0000). Read it twice from
# Linux: if the value changed, the PL fabric is alive. This reuses the exact PS /
# LPD / reset setup proven reachable for uart_echo.
#
# It deliberately reuses design_name=uart_echo_bd so the output bitstream is named
# uart_echo_bd_wrapper.bit.bin — matching the device-tree overlay/app already on
# the board, so you only swap the .bit.bin (no new overlay). The Vivado PROJECT is
# separate (pltest) so it doesn't clobber the uart_echo build.
#
# Build:  vivado -mode batch -source vivado/build_pltest.tcl

set proj_name   pltest
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c
set pl_clk_mhz  100

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]

if {[info exists ::env(USERPROFILE)]} {
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

add_files -norecurse [list [file join $rtl_dir pl_heartbeat.v]]
update_compile_order -fileset sources_1

create_bd_design $design_name

# Zynq US+ PS with board preset; enable pl_clk0 + the LPD AXI master (GP2).
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $pl_clk_mhz \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
] $ps

# Free-running counter + AXI GPIO (32-bit, all inputs) reading it.
set hb   [create_bd_cell -type module -reference pl_heartbeat u_hb]
set gpio [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_gpio:* axi_gpio_0]
set_property -dict [list CONFIG.C_GPIO_WIDTH {32} CONFIG.C_ALL_INPUTS {1}] $gpio

# PS -> AXI GPIO over LPD; automation builds interconnect + reset + clock + address.
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config { \
    Master      {/zynq_ultra_ps_e_0/M_AXI_HPM0_LPD} \
    Clk_xbar    {Auto} \
    Clk_master  {Auto} \
    Clk_slave   {Auto} \
    intc_ip     {Auto} \
    master_apm  {0} \
} [get_bd_intf_pins axi_gpio_0/S_AXI]

# Tie the auto-reset's dcm_locked high so the design isn't held in reset.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]

# Counter clock + counter -> GPIO inputs.
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins $hb/clk]
connect_bd_net [get_bd_pins $hb/count] [get_bd_pins $gpio/gpio_io_i]

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
