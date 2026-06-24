# vivado/ip/gen_aclkgt_gt.tcl
#
# Create + configure + generate the gtwizard_ultrascale GT Wizard IP "aclkgt_gt"
# for the KR260 SFP+ GTH (GTHE4_CHANNEL_X1Y12, Bank 224), for our custom 8b/10b
# link: 1.25 Gbps, 156.25 MHz refclk, 16-bit user data, comma 0xBC (K28.5),
# shared-logic-in-core (usrclk2 are IP OUTPUTS), near-end loopback available.
#
# Run: & "C:\Xilinx\Vivado\2024.2\bin\vivado.bat" -mode batch -source vivado\ip\gen_aclkgt_gt.tcl
#
# IMPORTANT: config is applied in STAGES, and after each stage we get_property and
# HARD-FAIL (exit 1) if it did not persist. A prior version used one big
# set_property -dict that failed atomically and silently left the IP at defaults
# (RAW / 10.3125 / 32-bit / EXAMPLE_DESIGN). Never trust set_property; verify it.

set part        xck26-sfvc784-2LV-c
set ip_name     aclkgt_gt
set script_dir  [file dirname [file normalize [info script]]]
set ip_repo_dir $script_dir
set scratch_dir [file join $::env(USERPROFILE) kria-builds aclkgt_ipgen]

proc expect_cfg {ip key want} {
    set got [get_property CONFIG.$key [get_ips $ip]]
    if {$got ne $want} {
        puts "FATAL: CONFIG.$key = '$got', expected '$want' (config did not persist)"
        exit 1
    }
    puts "  OK CONFIG.$key = $got"
}

create_project -force aclkgt_ipgen $scratch_dir -part $part
create_ip -name gtwizard_ultrascale -vendor xilinx.com -library ip -version 1.7 \
    -module_name $ip_name -dir $ip_repo_dir

# --- Stage 1: transceiver basics (line rate, refclk, encoding, widths) ---
set_property -dict [list \
    CONFIG.CHANNEL_ENABLE      {X0Y6} \
    CONFIG.TX_LINE_RATE        {1.25} \
    CONFIG.RX_LINE_RATE        {1.25} \
    CONFIG.TX_REFCLK_FREQUENCY {156.25} \
    CONFIG.RX_REFCLK_FREQUENCY {156.25} \
    CONFIG.TX_DATA_ENCODING    {8B10B} \
    CONFIG.RX_DATA_DECODING    {8B10B} \
    CONFIG.TX_USER_DATA_WIDTH  {16} \
    CONFIG.RX_USER_DATA_WIDTH  {16} \
] [get_ips $ip_name]
puts "=== verify stage 1 ==="
expect_cfg $ip_name TX_DATA_ENCODING 8B10B
expect_cfg $ip_name RX_DATA_DECODING 8B10B
expect_cfg $ip_name TX_USER_DATA_WIDTH 16
expect_cfg $ip_name TX_LINE_RATE 1.25

# --- Stage 2: clocking (shared logic in core) + freerun ---
set_property -dict [list \
    CONFIG.LOCATE_TX_USER_CLOCKING {CORE} \
    CONFIG.LOCATE_RX_USER_CLOCKING {CORE} \
    CONFIG.LOCATE_RESET_CONTROLLER {CORE} \
    CONFIG.FREERUN_FREQUENCY       {50} \
] [get_ips $ip_name]
puts "=== verify stage 2 ==="
expect_cfg $ip_name LOCATE_TX_USER_CLOCKING CORE
expect_cfg $ip_name LOCATE_RX_USER_CLOCKING CORE

# --- Stage 2c: RX buffer BYPASS ---
# The continuous gigabit-ACLK stream is received from a board with an INDEPENDENT
# 156.25 MHz oscillator (no idle to clock-correct on). With the RX elastic buffer
# (RX_BUFFER_MODE=1) it slips on the ppm offset -> disparity errors every frame.
# Bypass it (RX_BUFFER_MODE=0) so the RX user logic runs on the recovered clock;
# the in-core single-lane bypass controller does the phase-align + auto-retry.
set_property CONFIG.RX_BUFFER_MODE {0} [get_ips $ip_name]
set_property CONFIG.LOCATE_RX_BUFFER_BYPASS_CONTROLLER {CORE} [get_ips $ip_name]
puts "=== verify stage 2c (rx buffer bypass) ==="
expect_cfg $ip_name RX_BUFFER_MODE 0
# RX_BUFFER_BYPASS_MODE stays MULTI for a single channel (IP-managed, not TCL-settable);
# with one lane the bypass controller self-masters that channel.
puts "  RX_BUFFER_BYPASS_MODE = [get_property CONFIG.RX_BUFFER_BYPASS_MODE [get_ips $ip_name]] (IP-managed)"
expect_cfg $ip_name LOCATE_RX_BUFFER_BYPASS_CONTROLLER CORE

# --- Stage 2b: ENABLE K28.5 comma detection + alignment ---
# Without this the IP defaults comma detect to OFF (P/M_ENABLE=false, MASK=0), so the
# RX never detects the 0xBC (K28.5) comma, never byte-aligns, and decodes garbage.
# Values 0101111100 / 1010000011 are the standard UltraScale K28.5 P/M comma codes.
set_property -dict [list \
    CONFIG.RX_COMMA_P_ENABLE      {true} \
    CONFIG.RX_COMMA_M_ENABLE      {true} \
    CONFIG.RX_COMMA_P_VAL         {0101111100} \
    CONFIG.RX_COMMA_M_VAL         {1010000011} \
    CONFIG.RX_COMMA_MASK          {1111111111} \
    CONFIG.RX_COMMA_DOUBLE_ENABLE {false} \
    CONFIG.RX_COMMA_ALIGN_WORD    {1} \
] [get_ips $ip_name]
puts "=== verify stage 2b (comma) ==="
expect_cfg $ip_name RX_COMMA_P_ENABLE true
expect_cfg $ip_name RX_COMMA_M_ENABLE true
# the EFFECTIVE generated enable must be 1 (the .xci's C_* value is what the core uses)
set cpe [get_property CONFIG.C_RX_COMMA_P_ENABLE [get_ips $ip_name]]
set cme [get_property CONFIG.C_RX_COMMA_M_ENABLE [get_ips $ip_name]]
if {$cpe ne "1" || $cme ne "1"} {
    puts "FATAL: comma detect NOT effectively enabled (C_RX_COMMA_P_ENABLE=$cpe C_RX_COMMA_M_ENABLE=$cme)"
    exit 1
}
puts "  OK C_RX_COMMA_P_ENABLE=$cpe C_RX_COMMA_M_ENABLE=$cme"

# --- Stage 3: optional ports (loopback + 8b10b enables + comma + status) ---
# ctrl0/1/2/3 are auto-exposed by 8B10B; we enable loopback, the enable strobes,
# the comma-align enables, the byte-alignment/comma-detect status, and powergood.
set_property CONFIG.ENABLE_OPTIONAL_PORTS \
    {loopback_in tx8b10ben_in rx8b10ben_in rxcommadeten_in rxmcommaalignen_in rxpcommaalignen_in rxbyteisaligned_out rxbyterealign_out rxcommadet_out gtpowergood_out gtwiz_reset_rx_cdr_stable_out rxpmaresetdone_out txpmaresetdone_out rxpolarity_in txpolarity_in} \
    [get_ips $ip_name]
puts "=== verify stage 3 ==="
set opt [get_property CONFIG.ENABLE_OPTIONAL_PORTS [get_ips $ip_name]]
puts "  ENABLE_OPTIONAL_PORTS = $opt"
if {[lsearch $opt loopback_in] < 0} {
    puts "FATAL: loopback_in not enabled (ENABLE_OPTIONAL_PORTS did not persist)"
    exit 1
}

# --- Generate ---
generate_target {instantiation_template synthesis} [get_ips $ip_name]
catch {export_ip_user_files -of_objects [get_ips $ip_name] -no_script -sync -force -quiet}

# --- Dump the REAL instantiation port list (.veo) ---
set veo [file join $ip_repo_dir $ip_name ${ip_name}.veo]
if {[file exists $veo]} {
    puts "===== REAL PORT LIST ($veo) ====="
    set fh [open $veo r]; puts [read $fh]; close $fh
} else {
    puts "WARNING: .veo not found at $veo"
}
puts "INFO: gen_aclkgt_gt.tcl complete (config verified)."
