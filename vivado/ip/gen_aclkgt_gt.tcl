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

# Start from a CLEAN slate: if vivado/ip/aclkgt_gt/ already exists from a prior run,
# create_ip + generate_target see the IP as "already present" and SKIP rewriting it, so
# config edits (e.g. RX_EQ_MODE) silently never reach the regenerated netlist (proven: the
# .xci + synth/ stay frozen at the first run's mtime). Deleting the dir forces a true
# fresh customization, exactly like a from-scratch generate.
set ip_out_dir [file join $ip_repo_dir $ip_name]
if {[file exists $ip_out_dir]} {
    puts "INFO: removing stale generated IP at $ip_out_dir for a clean regenerate"
    file delete -force $ip_out_dir
}
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

# --- RX equalizer = LPM: this is set as the FINAL stage (see Stage 4 below), NOT here. ---
# The wizard DEFAULTS RX_EQ_MODE=AUTO with INS_LOSS_NYQ=20 dB, which resolves to DFE
# (RXLPMEN=0) tuned for a long, lossy COPPER backplane. That is wrong for our channel: a
# few cm of PCB into an SFP that regenerates the optical signal (~1-2 dB loss). DFE needs
# inter-symbol interference to adapt its taps; on a wide-open, low-ISI 1.25G optical eye it
# has nothing to lock to, its adaptation wanders and CLOSES the eye -> 8b10b disparity
# errors, no byte-align on EVERY real optical link (HW: both boards fail their own optical
# self-loop identically; M0 internal PMA loopback hides it). Xilinx guidance: LPM for
# low-loss/low-rate links, DFE only above ~14 dB. 1.25G optical is the canonical LPM case.
# WHY LAST: setting RX_EQ_MODE here (before the clocking/comma/optional-port stages) gets
# silently RE-RESOLVED back to AUTO/DFE by a later stage (proven: a regen "passed" in-session
# yet the netlist kept RXLPMEN=0 and the bitstream was byte-identical). Set it LAST, after
# every other CONFIG, so nothing clobbers it -- then VERIFY at the netlist level (Stage 4).

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

# --- Stage 2c: RX elastic buffer ENABLED (default, RX_BUFFER_MODE=1) ---
# REVERTED from RX buffer bypass. Bypass broke comma/byte alignment: on the self-test
# build the GT never byte-aligned (byteali=0) even in clean INTERNAL PMA loopback, which
# M0 decodes perfectly with the buffer enabled. Buffer bypass + comma alignment need
# special sequencing the in-core controller does not provide here, and the elastic-buffer-
# slip theory it was meant to fix was never supported by evidence. Keep the buffer (the
# M0-proven config); the small two-board ppm offset is well within the elastic buffer.
expect_cfg $ip_name RX_BUFFER_MODE 1

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
# txdiffctrl_in / txpostcursor_in / txprecursor_in: expose the TX driver swing + pre/post
# emphasis as runtime PORTS so the wrapper can SWEEP them live (via GT_CTRL bits) to hunt a
# TX eye the real SFP link locks on. The HW failure is equalizer- and power-independent, which
# points at the TX-into-SFP drive -- the one analog block near-end PMA loopback never tests.
# Available because TX_DIFF_SWING_EMPH_MODE=CUSTOM. Every wrapper MUST drive these or they float.
# rxbufstatus_out: expose the RX elastic-buffer status so the wrapper can latch a slip sticky.
# 3'b101=underflow / 3'b110=overflow => the buffer slipped a word (breaks 8b10b disparity). This
# is the one direct measurement that settles the buffer-slip-vs-analog question on a two-board
# link; on the shared-refclk self-test it stays 000 (the buffer cannot slip mesochronously).
set_property CONFIG.ENABLE_OPTIONAL_PORTS \
    {loopback_in tx8b10ben_in rx8b10ben_in rxcommadeten_in rxmcommaalignen_in rxpcommaalignen_in rxbyteisaligned_out rxbyterealign_out rxcommadet_out rxbufstatus_out gtpowergood_out gtwiz_reset_rx_cdr_stable_out rxpmaresetdone_out txpmaresetdone_out rxpolarity_in txpolarity_in txdiffctrl_in txpostcursor_in txprecursor_in} \
    [get_ips $ip_name]
puts "=== verify stage 3 ==="
set opt [get_property CONFIG.ENABLE_OPTIONAL_PORTS [get_ips $ip_name]]
puts "  ENABLE_OPTIONAL_PORTS = $opt"
if {[lsearch $opt loopback_in] < 0} {
    puts "FATAL: loopback_in not enabled (ENABLE_OPTIONAL_PORTS did not persist)"
    exit 1
}
foreach p {txdiffctrl_in txpostcursor_in txprecursor_in} {
    if {[lsearch $opt $p] < 0} {
        puts "FATAL: $p not enabled (TX-driver sweep ports did not persist)"
        exit 1
    }
}
if {[lsearch $opt rxbufstatus_out] < 0} {
    puts "FATAL: rxbufstatus_out not enabled (buffer-slip telemetry port did not persist)"
    exit 1
}

# --- Stage 4: RX equalizer = LPM (set LAST so no later stage re-resolves it to DFE) ---
# See the long note near Stage 2. LPM is correct for our clean, low-loss 1.25G optical link;
# AUTO/DFE was mis-adapting on the wide-open eye and is the proven cause of the disperr /
# no-byte-align failure over real optics. INS_LOSS_NYQ=1 reflects the real ~1 dB electrical
# path (vs the 20 dB backplane default) so AUTO logic / LPM adaption start from sane loss.
set_property -dict [list \
    CONFIG.RX_EQ_MODE   {LPM} \
    CONFIG.INS_LOSS_NYQ {1} \
] [get_ips $ip_name]
expect_cfg $ip_name RX_EQ_MODE LPM

# --- Generate ---
generate_target {instantiation_template synthesis} [get_ips $ip_name]
catch {export_ip_user_files -of_objects [get_ips $ip_name] -no_script -sync -force -quiet}

# --- Stage 4 NETLIST verify: the CONFIG read-back LIES (it echoes LPM even when the IP
# reverts to DFE on generate). The only trustworthy check is the generated synthesis source:
# LPM bakes `.rxlpmen_in(1'H1)` (RXLPMEN=1). DFE leaves it `1'H0`. Hard-fail if not LPM. ---
set synth_v [file join $ip_repo_dir $ip_name synth ${ip_name}.v]
if {![file exists $synth_v]} {
    puts "FATAL: generated synth source not found at $synth_v (cannot verify RXLPMEN)"
    exit 1
}
set fh [open $synth_v r]; set synth_txt [read $fh]; close $fh
if {[string match -nocase "*rxlpmen_in(1'H1)*" $synth_txt]} {
    puts "  OK NETLIST: RXLPMEN tied high -> LPM equalizer active (.rxlpmen_in(1'H1))"
} elseif {[string match -nocase "*rxlpmen_in(1'H0)*" $synth_txt]} {
    puts "FATAL: NETLIST has RXLPMEN=0 (DFE) -- RX_EQ_MODE=LPM did NOT take. Do NOT build."
    exit 1
} else {
    puts "FATAL: could not find rxlpmen_in tie in $synth_v -- verify RXLPMEN manually before building."
    exit 1
}

# TX-driver sweep ports must be REAL module inputs, not internal constant ties. When exposed,
# the top wrapper declares `input ... txdiffctrl_in`; when the optional-port enable silently
# reverted (as rxlpmen_in once did), the wrapper instead ties `.txdiffctrl_in(5'H..)`. Confirm
# the input declaration exists; hard-fail otherwise so we never build a non-sweepable bitstream.
foreach p {txdiffctrl_in txpostcursor_in txprecursor_in} {
    if {[regexp "input\[^;\]*${p}\[^a-zA-Z0-9_\]" $synth_txt]} {
        puts "  OK NETLIST: $p exposed as a module input (runtime-sweepable)"
    } else {
        puts "FATAL: $p is NOT a module input (optional-port enable reverted). Do NOT build."
        exit 1
    }
}

# rxbufstatus_out must be a REAL module output (buffer-slip telemetry), not dropped.
if {[regexp "output\[^;\]*rxbufstatus_out\[^a-zA-Z0-9_\]" $synth_txt]} {
    puts "  OK NETLIST: rxbufstatus_out exposed as a module output (buffer-slip telemetry)"
} else {
    puts "FATAL: rxbufstatus_out is NOT a module output (optional-port enable reverted). Do NOT build."
    exit 1
}

# --- Dump the REAL instantiation port list (.veo) ---
set veo [file join $ip_repo_dir $ip_name ${ip_name}.veo]
if {[file exists $veo]} {
    puts "===== REAL PORT LIST ($veo) ====="
    set fh [open $veo r]; puts [read $fh]; close $fh
} else {
    puts "WARNING: .veo not found at $veo"
}
puts "INFO: gen_aclkgt_gt.tcl complete (config verified)."
