## constraints/kr260_aclk.xdc - ACLK-Lite Manchester input
##
## ACLK-Lite rides the same TTL baseband interface as TCLK, so the Manchester line
## enters on KR260 PMOD1 pin 1 = package H12, LVCMOS33 (the same physical pin the
## TCLK build uses). Verify the connector position against the carrier-card silkscreen.

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports aclk]

## Clock-alive scope diagnostics (temporary): divided clk_os + pl_clk0 to PMOD1 pins.
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clkos_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports clk100_dbg]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports cdc_dbg]
set_property -dict {PACKAGE_PIN D11 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Asynchronous clock groups.
##
## The clk_wiz MMCM makes the single ~120 MHz oversample clock (clk_out1) from
## pl_clk0; it shares pl_clk0 as a physical source but is NOT phase-comparable to the
## ~100 MHz PS/AXI clock (clk_pl_*). Every PS<->rx crossing goes through the readout's
## async FIFO (gray pointers + 2-FF syncs), safe by construction. Without this the
## tool times those CDC paths and reports large false negative slack. Declaring the
## domains asynchronous excludes those crossings.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
