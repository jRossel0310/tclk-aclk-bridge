# vivado/build.tcl — scripted RTL -> bitstream for the Kria KR260
#
# Builds a Zynq UltraScale+ block design that lets you test the custom uart_echo
# RTL over the network (no external adapter): the PS reaches an AXI UART Lite over
# AXI, and that UART Lite's tx/rx are cross-wired to uart_echo's serial_in/out, so
# the two UARTs loop back INSIDE the PL. Send a byte from Linux -> it passes
# through our RTL -> echoes back. Then runs synthesis, implementation, bitstream.
#
# Run it in batch mode (the hw.ps1 / hw.sh wrappers do this for you):
#     vivado -mode batch -source vivado/build.tcl
#
# Everything lands under vivado/build/ (git-ignored). The block design is also
# exported as vivado/uart_echo_bd.tcl so it is reproducible from source control.

# --- Settings ---------------------------------------------------------------
set proj_name   uart_echo
set design_name uart_echo_bd
set top_module  uart_echo_bd_top
set part        xck26-sfvc784-2LV-c
set pl_clk_mhz  100

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260.xdc]

# Vivado's block-design (IP Integrator) flow is unreliable when the project path
# contains spaces — and this repo lives under ".../Summer 2026/...". So build in a
# space-free directory (a throwaway artifact dir; sources stay in the repo).
# Override the location with the KRIA_BUILD_DIR env var if you like.
if {[info exists ::env(KRIA_BUILD_DIR)] && [string length $::env(KRIA_BUILD_DIR)] > 0} {
    set build_dir $::env(KRIA_BUILD_DIR)
} elseif {[info exists ::env(USERPROFILE)]} {
    set build_dir [file join $::env(USERPROFILE) kria-builds $proj_name]
} elseif {[info exists ::env(HOME)]} {
    set build_dir [file join $::env(HOME) kria-builds $proj_name]
} else {
    set build_dir [file join $script_dir build]
}
puts "INFO: building in $build_dir"

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
    [file join $rtl_dir uart_echo_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]

# Wrap paths in [list ...] so a space in the project path (e.g. "Summer 2026")
# is treated as one file, not split into two by add_files' list parsing.
add_files -fileset constrs_1 -norecurse [list $xdc_file]

# Parse the RTL now so uart_echo_top is known as a module before we reference it
# as a block-design cell below (otherwise batch mode can't find it).
update_compile_order -fileset sources_1

# --- Block design: PS + reset + our RTL -------------------------------------
create_bd_design $design_name

# Zynq UltraScale+ MPSoC, configured from the board preset.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e \
        -config {apply_board_preset 1} $ps
}
# Enable pl_clk0 and the M_AXI_HPM0_LPD master (GP2). The LPD (low-power domain)
# AXI master is always-on and is the most reliably-exposed PS->PL control path on
# the Kria platform; the FPD ports (GP0/GP1) are left off so their unconnected
# aclks don't fail validation.
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ $pl_clk_mhz \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
] $ps

# Our custom UART echo, as an RTL module reference (no IP packaging).
set echo [create_bd_cell -type module -reference $top_module u_echo]

# AMD AXI UART Lite: a PS-accessible UART fixed at 8-N-1 / 115200 to match our
# RTL. Its serial tx/rx are cross-wired to our echo so the two UARTs talk to each
# other entirely INSIDE the PL — the PS drives the test over AXI, no pins needed.
set ul [create_bd_cell -type ip -vlnv xilinx.com:ip:axi_uartlite:* axi_uartlite_0]
set_property -dict [list CONFIG.C_BAUDRATE {115200}] $ul

# Let Vivado's connection automation wire the PS master to the UART Lite slave. It
# builds the SmartConnect + a proc_sys_reset and hooks up pl_clk0, the interface-
# ASSOCIATED resets, dcm_locked, and the address map — i.e. all the PS<->PL AXI
# plumbing that is easy to get subtly wrong by hand (manual wiring left the PS
# master interface without a proper associated reset, so the slave never replied).
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config { \
    Master      {/zynq_ultra_ps_e_0/M_AXI_HPM0_LPD} \
    Clk_xbar    {Auto} \
    Clk_master  {Auto} \
    Clk_slave   {Auto} \
    intc_ip     {Auto} \
    master_apm  {0} \
} [get_bd_intf_pins axi_uartlite_0/S_AXI]

# Attach our echo to the same pl_clk0 + active-high reset the automation set up.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]

# The auto-created proc_sys_reset leaves dcm_locked UNCONNECTED, which (with no
# clock wizard to drive it) holds every reset asserted forever — so the whole PL
# design sits in reset and AXI reads return 0x00. Tie dcm_locked high so the
# resets actually release. (Confirmed on hardware: this was the final blocker.)
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]

connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins $echo/clk]
connect_bd_net [get_bd_pins $rst/peripheral_reset]     [get_bd_pins $echo/reset]

# --- Internal UART loopback: UART Lite <-> our echo ---
# PS-sent byte: UART Lite tx -> our serial_in (into our uart_receiver).
# Echo back:    our serial_out -> UART Lite rx (read back by the PS).
connect_bd_net [get_bd_pins $ul/tx]           [get_bd_pins $echo/serial_in]
connect_bd_net [get_bd_pins $echo/serial_out] [get_bd_pins $ul/rx]

# --- Finalize the design ----------------------------------------------------
# assign_bd_address logs the assignment, e.g.:
#   Slave segment '/axi_uartlite_0/S_AXI/Reg' ... at <0xA000_0000 [ 64K ]>
# That base address is what the device-tree overlay needs (see vivado/README.md).
assign_bd_address
regenerate_bd_layout
validate_bd_design
save_bd_design

# Export the block design as Tcl for source control (regenerates it from scratch).
write_bd_tcl -force [file join $script_dir uart_echo_bd.tcl]

# HDL wrapper around the BD, set as top. -import lets Vivado generate AND add the
# wrapper itself, so we don't hardcode a version-specific generated path.
set bd_file [get_files ${design_name}.bd]
make_wrapper -files $bd_file -top -import
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
