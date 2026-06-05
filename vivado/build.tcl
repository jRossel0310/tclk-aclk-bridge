# vivado/build.tcl — scripted RTL -> bitstream for the Kria KR260
#
# Builds a Zynq UltraScale+ block design (PS supplies pl_clk0 + reset to the PL),
# drops uart_echo_top in as an RTL module, wires serial_in/serial_out out to the
# PMOD pins from constraints/kr260.xdc, then runs synthesis, implementation, and
# write_bitstream.
#
# Run it in batch mode (the hw.ps1 / hw.sh wrappers do this for you):
#     vivado -mode batch -source vivado/build.tcl
#
# Everything lands under vivado/build/ (git-ignored). The block design is also
# exported as vivado/uart_echo_bd.tcl so it is reproducible from source control.

# --- Settings ---------------------------------------------------------------
set proj_name   uart_echo
set design_name uart_echo_bd
set top_module  uart_echo_top
set part        xck26-sfva676-2LV-c
set pl_clk_mhz  100

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set build_dir  [file join $script_dir build]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260.xdc]

# --- Project ----------------------------------------------------------------
create_project -force $proj_name $build_dir -part $part

# Apply the KR260 board file if it is installed (configures the PS preset). The
# build still works part-only, but installing the board file is recommended.
set bp [get_board_parts -quiet -filter {NAME =~ "*kr260*"}]
if {[llength $bp] > 0} {
    set_property board_part [lindex $bp 0] [current_project]
    puts "INFO: using board_part [lindex $bp 0]"
} else {
    puts "WARNING: KR260 board file not found. Install it via Vivado Store >"
    puts "WARNING: Boards > Kria KR260, or the build will use the raw part only."
}

# --- RTL sources ------------------------------------------------------------
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir uart_receiver.sv] \
    [file join $rtl_dir uart_transmitter.sv] \
    [file join $rtl_dir fifo.sv] \
    [file join $rtl_dir uart_echo_top.sv] \
]
set_property file_type SystemVerilog [get_files *.sv]

add_files -fileset constrs_1 -norecurse $xdc_file

# --- Block design: PS + reset + our RTL -------------------------------------
create_bd_design $design_name

# Zynq UltraScale+ MPSoC, configured from the board preset.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
        -config {apply_board_preset 1} $ps
}
# Enable a single PL clock at pl_clk_mhz and expose pl_resetn.
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $pl_clk_mhz \
] $ps

# Reset bridge: turns the PS active-low pl_resetn into the active-high,
# clk-synchronous reset our RTL expects.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* proc_sys_reset_0]
set_property -dict [list CONFIG.C_EXT_RESET_HIGH {0}] $rst

# uart_echo_top as an RTL module reference inside the BD (no IP packaging).
set echo [create_bd_cell -type module -reference $top_module u_echo]

# Wire clock + reset.
connect_bd_net [get_bd_pins $ps/pl_clk0]              [get_bd_pins $rst/slowest_sync_clk]
connect_bd_net [get_bd_pins $ps/pl_clk0]              [get_bd_pins $echo/clk]
connect_bd_net [get_bd_pins $ps/pl_resetn0]           [get_bd_pins $rst/ext_reset_in]
connect_bd_net [get_bd_pins $rst/peripheral_reset]    [get_bd_pins $echo/reset]

# Expose the UART pins as external ports (names must match the XDC).
create_bd_port -dir I serial_in
create_bd_port -dir O serial_out
connect_bd_net [get_bd_ports serial_in]  [get_bd_pins $echo/serial_in]
connect_bd_net [get_bd_ports serial_out] [get_bd_pins $echo/serial_out]

# --- Finalize the design ----------------------------------------------------
regenerate_bd_layout
validate_bd_design
save_bd_design

# Export the block design as Tcl for source control (regenerates it from scratch).
write_bd_tcl -force [file join $script_dir uart_echo_bd.tcl]

# HDL wrapper around the BD, set as top.
set bd_file [get_files ${design_name}.bd]
make_wrapper -files $bd_file -top
add_files -norecurse [file join $build_dir ${proj_name}.gen sources_1 bd $design_name hdl ${design_name}_wrapper.v]
set_property top ${design_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

# --- Synthesis -> implementation -> bitstream -------------------------------
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
