## constraints/kr260_tclk.xdc - real TCLK input
##
## The biphase-mark TCLK line (3.3V baseband from the external front-end) enters
## on KR260 PMOD1 pin 1 = package H12, LVCMOS33. Verify the physical connector
## position against the carrier-card silkscreen; the package pin (H12) is correct.

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports tclk]

## Clock-alive scope diagnostics (temporary): divided clk_40m + pl_clk0 to PMOD1 pins.
## clk40_dbg = PMOD1 pin3 = package E10 ; clk100_dbg = PMOD1 pin4 = package E12.
## Verify the physical connector positions against the carrier-card silkscreen.
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clk40_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports clk100_dbg]
## cdc_dbg = a fresh cdc_gray_count's output = PMOD1 pin5 = package D10.
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports cdc_dbg]
## dbg_hb = the DEEP readout heartbeat[12] = PMOD1 pin6 = package D11.
set_property -dict {PACKAGE_PIN D11 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Asynchronous clock groups.
##
## The clk_wiz MMCM makes the 80/40 MHz receive clocks (clk_out1/clk_out2) from
## pl_clk0, so they share pl_clk0 as a physical source but are NOT phase-comparable
## to the ~100 MHz PS/AXI clock (clk_pl_*): 80 and 40 have no common period with 100.
## Every path crossing between the PS/AXI domain and the receive domain goes through
## the readout's async FIFO (gray-code pointers + 2-FF synchronizers), which is safe
## by construction. Without this constraint the tool TIMES those CDC paths, reports
## huge false negative slack (seen: WNS -3.3 ns, ~5000 failing endpoints, all
## "No Common Period / unsafe"), and the router burns extra global iterations chasing
## violations that aren't real. Declaring the two domains asynchronous excludes those
## crossings. clk_out1 (80) and clk_out2 (40) stay in ONE group: they are 2:1
## phase-related from the same MMCM and their crossing already meets timing cleanly.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
