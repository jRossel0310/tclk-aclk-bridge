# vivado/ip/gen_aclkgt_gt.tcl
#
# Create and configure the gtwizard_ultrascale GT Wizard IP for the KR260 SFP+ GTH
# transceiver (GTHE4_CHANNEL_X1Y12, Bank 224, Quad X0Y1, Lane X0Y6).
#
# Usage (batch mode):
#   & "C:\Xilinx\Vivado\2024.2\bin\vivado.bat" -mode batch \
#       -source vivado\ip\gen_aclkgt_gt.tcl 2>&1 | Tee-Object vivado\ip\gen.log
#
# This script ONLY creates + configures + generates the IP instantiation template and
# synthesis stub.  It does NOT synthesise or implement a design.
#
# Target part: xck26-sfvc784-2LV-c
# GT: GTHE4, Quad X0Y1, Lane X0Y6 (MGTREFCLK0 = Y6/Y5, 156.25 MHz)
# Link: 1.25 Gbps, 8b/10b, 16-bit user data, shared-logic-in-core.

set part     xck26-sfvc784-2LV-c
set ip_name  aclkgt_gt

# ---------------------------------------------------------------------------
# Scratch project directory (kept OUT of the repo tree -- see .gitignore)
# ---------------------------------------------------------------------------
set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname [file dirname $script_dir]]

if {[info exists ::env(USERPROFILE)]} {
    set scratch_dir [file join $::env(USERPROFILE) kria-builds ipscratch]
} else {
    set scratch_dir [file join $root_dir build kria ipscratch]
}

# IP output goes into vivado/ip/ (text .xci is committed; large synth products are not)
set ip_repo_dir [file join $script_dir]

puts "INFO: scratch project dir = $scratch_dir"
puts "INFO: IP output dir       = $ip_repo_dir"

# ---------------------------------------------------------------------------
# Create a throwaway in-memory-style project
# ---------------------------------------------------------------------------
create_project -force ipscratch $scratch_dir -part $part

# ---------------------------------------------------------------------------
# Create the GT Wizard IP
# ---------------------------------------------------------------------------
create_ip \
    -name gtwizard_ultrascale \
    -vendor xilinx.com \
    -library ip \
    -version * \
    -module_name $ip_name \
    -dir $ip_repo_dir

# ---------------------------------------------------------------------------
# Discover what CONFIG properties this Vivado version exposes
# (printed to log; useful when iterating on CONFIG errors)
# ---------------------------------------------------------------------------
puts "INFO: Listing all CONFIG.* properties on $ip_name ..."
set all_props [list_property [get_ips $ip_name]]
foreach p $all_props {
    if {[string match CONFIG.* $p]} {
        set val [get_property $p [get_ips $ip_name]]
        puts "  $p = $val"
    }
}

# ---------------------------------------------------------------------------
# Configure the IP
# ---------------------------------------------------------------------------
# Notes:
#   - CHANNEL_ENABLE: X0Y6 selects GTHE4_CHANNEL_X1Y12 (the KR260 SFP lane).
#   - TX/RX_LINE_RATE: 1.25 Gbps (156.25 MHz refclk x 8; clean PLL ratio).
#   - TX/RX_REFCLK_FREQUENCY: 156.25 MHz (MGTREFCLK0_224, Y6/Y5).
#   - Encoding: 8b/10b TX+RX; 16-bit user data width; 2-bit K per 16-bit word.
#   - Shared logic IN CORE: IP provides QPLL + usrclk buffers; design gets
#       gtwiz_userclk_{tx,rx}_usrclk2_out rather than driving usrclk externally.
#   - FREERUN_FREQUENCY: 100 MHz (PS pl_clk0).
#   - Comma: K28.5 (0xBC). P-comma = 0101111100, M-comma = 1010000011.
#   - Optional ports: loopback_in, gtpowergood_out, rxbyteisaligned_out,
#       rxcommadet_out, rxbyterealign_out, rxpmaresetdone_out, txpmaresetdone_out,
#       gtwiz_reset_rx_cdr_stable_out, txctrl0/1/2, rxctrl0/1/2/3.

set_property -dict [list \
    CONFIG.CHANNEL_ENABLE                    {X0Y6} \
    CONFIG.TX_LINE_RATE                      {1.25} \
    CONFIG.TX_REFCLK_FREQUENCY               {156.25} \
    CONFIG.TX_USER_DATA_WIDTH                {16} \
    CONFIG.TX_DATA_ENCODING                  {8B10B} \
    CONFIG.TX_INT_DATA_WIDTH                 {20} \
    CONFIG.RX_LINE_RATE                      {1.25} \
    CONFIG.RX_REFCLK_FREQUENCY               {156.25} \
    CONFIG.RX_USER_DATA_WIDTH                {16} \
    CONFIG.RX_DATA_DECODING                  {8B10B} \
    CONFIG.RX_INT_DATA_WIDTH                 {20} \
    CONFIG.LOCATE_TX_USER_CLOCKING           {CORE} \
    CONFIG.LOCATE_RX_USER_CLOCKING           {CORE} \
    CONFIG.LOCATE_RESET_CONTROLLER           {CORE} \
    CONFIG.FREERUN_FREQUENCY                 {50} \
    CONFIG.RX_COMMA_P_ENABLE                 {TRUE} \
    CONFIG.RX_COMMA_M_ENABLE                 {TRUE} \
    CONFIG.RX_COMMA_DOUBLE_ENABLE            {FALSE} \
    CONFIG.RX_COMMA_P_VAL                    {0101111100} \
    CONFIG.RX_COMMA_M_VAL                    {1010000011} \
    CONFIG.RX_COMMA_MASK                     {1111111111} \
    CONFIG.RX_COMMA_ALIGN_WORD               {1} \
    CONFIG.RX_SLIDE_MODE                     {OFF} \
    CONFIG.ENABLE_OPTIONAL_PORTS             {loopback_in gtpowergood_out rxbyteisaligned_out rxcommadet_out rxbyterealign_out rxpmaresetdone_out txpmaresetdone_out gtwiz_reset_rx_cdr_stable_out txctrl0_in txctrl1_in txctrl2_in rxctrl0_out rxctrl1_out rxctrl2_out rxctrl3_out} \
] [get_ips $ip_name]

# ---------------------------------------------------------------------------
# Re-print config after set (confirmation)
# ---------------------------------------------------------------------------
puts "INFO: Key CONFIG values after set_property:"
foreach key {
    CHANNEL_ENABLE TX_LINE_RATE TX_REFCLK_FREQUENCY TX_USER_DATA_WIDTH TX_DATA_ENCODING
    RX_LINE_RATE RX_REFCLK_FREQUENCY RX_USER_DATA_WIDTH RX_DATA_DECODING
    LOCATE_TX_USER_CLOCKING LOCATE_RX_USER_CLOCKING LOCATE_RESET_CONTROLLER
    FREERUN_FREQUENCY
    RX_COMMA_P_ENABLE RX_COMMA_M_ENABLE RX_COMMA_P_VAL RX_COMMA_M_VAL RX_COMMA_ALIGN_WORD
} {
    catch {
        set val [get_property CONFIG.$key [get_ips $ip_name]]
        puts "  CONFIG.$key = $val"
    }
}

# ---------------------------------------------------------------------------
# Generate instantiation template + synthesis stub (no full synthesis)
# ---------------------------------------------------------------------------
puts "INFO: Generating instantiation_template and synthesis targets ..."
generate_target {instantiation_template synthesis} [get_ips $ip_name]

# Force the generated files to be written to the ip_repo_dir by exporting
# ip_user_files and synth templates.
puts "INFO: Exporting IP user files ..."
catch {export_ip_user_files -of_objects [get_ips $ip_name] -no_script -sync -force -quiet}

# ---------------------------------------------------------------------------
# Report the generated .veo path and print port list
# Search in ip_repo_dir, scratch ip_user_files, and any synth subdirectory
# ---------------------------------------------------------------------------
set search_roots [list \
    [file join $ip_repo_dir $ip_name] \
    [file join $scratch_dir ipscratch.ip_user_files ip $ip_name] \
    [file join $scratch_dir ipscratch.srcs sources_1 ip $ip_name] \
]

set veo_files {}
foreach sr $search_roots {
    set found [glob -nocomplain -directory $sr -type f "*.veo"]
    if {[llength $found] > 0} {
        set veo_files [concat $veo_files $found]
    }
    # Also check one level deep
    set found2 [glob -nocomplain [file join $sr * *.veo]]
    if {[llength $found2] > 0} {
        set veo_files [concat $veo_files $found2]
    }
}

# Fallback: find ANYWHERE under the project dir using glob
if {[llength $veo_files] == 0} {
    set veo_files [glob -nocomplain [file join $scratch_dir * ip $ip_name *.veo]]
}

if {[llength $veo_files] > 0} {
    set veo_path [lindex $veo_files 0]
    puts "INFO: Generated .veo: $veo_path"
    puts "==========================================================="
    puts "PORT LIST from $veo_path:"
    puts "==========================================================="
    set fh [open $veo_path r]
    puts [read $fh]
    close $fh
} else {
    puts "WARNING: Could not locate .veo file. Dumping all generated files:"
    foreach sr $search_roots {
        if {[file isdirectory $sr]} {
            foreach f [glob -nocomplain -directory $sr -type f *] {
                puts "  $f"
            }
        }
    }
    puts "INFO: Dumping xci for port reference:"
    set xci [file join $ip_repo_dir $ip_name ${ip_name}.xci]
    if {[file exists $xci]} {
        # Extract the instantiation ports from the XCI JSON
        set fh [open $xci r]
        set xci_content [read $fh]
        close $fh
        # Print just enough to identify the template ports section
        puts "INFO: XCI file size = [file size $xci] bytes (see vivado/ip/aclkgt_gt/aclkgt_gt.xci)"
    }
}

puts "INFO: gen_aclkgt_gt.tcl complete."
