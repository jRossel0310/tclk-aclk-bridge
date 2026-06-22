## constraints/kr260_clk.xdc - unified ACLK/TCLK serial input
##
## The Manchester serial line (real TCLK or real ACLK-Lite, 3.3V baseband) enters on
## KR260 PMOD1 pin 1 = package H12, LVCMOS33 (same physical pin the TCLK build uses).
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports clkline]

## Clock-alive scope diagnostics (temporary): divided clk_40m + pl_clk0 to PMOD1 pins.
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clk40_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports clk100_dbg]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports cdc_dbg]
set_property -dict {PACKAGE_PIN D11 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Asynchronous clock groups: the clk_wiz MMCM makes 80/40 MHz (clk_out1/clk_out2) from
## pl_clk0; they are not phase-comparable to the ~100 MHz PS/AXI clock (clk_pl_*). Every
## crossing goes through the readout's async FIFO, so declare the domains asynchronous.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
