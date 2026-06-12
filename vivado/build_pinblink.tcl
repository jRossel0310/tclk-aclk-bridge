# vivado/build_pinblink.tcl — H12 pin-toggle bring-up test for the KR260.
#
# This is the proven "is the PL running?" design (vivado/build_pltest.tcl: PS +
# pl_clk0 + LPD GP2 + AXI GPIO heartbeat at 0x8000_0000 + dcm_locked tied high)
# with ONE thing added: a slow blinker (rtl/pin_blink.v) driving package pin H12
# (PMOD1 pin 1, LVCMOS33) as a 0.5 Hz square wave. Load it, then probe H12: it
# alternates 0 <-> 3.3V every second, confirming the pin works at 3.3V before the
# real TCLK design uses it. The heartbeat + AXI GPIO are kept so the SAME overlay
# loads and deploy/pltest.py still confirms the PL is configured and clocked.
#
# It deliberately reuses design_name=uart_echo_bd so the bitstream is named
# uart_echo_bd_wrapper.bit.bin — matching the device-tree overlay already on the
# board, so you only swap the .bit.bin (no new overlay). The Vivado PROJECT is
# separate (pinblink) so it does not clobber the uart_echo / pltest builds.
#
# Build:  vivado -mode batch -source vivado/build_pinblink.tcl
#    or:  .\hw.ps1 build -Tcl vivado\build_pinblink.tcl -Name pinblink   (PS, with AV retry)

set proj_name   pinblink
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c
set pl_clk_mhz  100

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_pinblink.xdc]

# Build in a space-free dir (IP Integrator breaks on spaces; repo is under
# "Summer 2026"). Honor KRIA_BUILD_DIR so the hw.ps1 wrapper can point us at it.
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

# RTL: the heartbeat (running-check) + the H12 blinker. Both plain Verilog so they
# can be block-design module references.
add_files -norecurse [list \
    [file join $rtl_dir pl_heartbeat.v] \
    [file join $rtl_dir pin_blink.v] \
]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
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

# Free-running counter + AXI GPIO (32-bit, all inputs) reading it (the proven
# "PL is running" path; read over UIO with deploy/pltest.py).
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

# H12 blinker: pl_clk0 -> pin_blink -> external output port pin_h12 (-> H12 in XDC).
set blink [create_bd_cell -type module -reference pin_blink u_blink]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins $blink/clk]
create_bd_port -dir O pin_h12
connect_bd_net [get_bd_pins $blink/pin] [get_bd_port pin_h12]

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
