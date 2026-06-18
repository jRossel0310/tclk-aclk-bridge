## constraints/kr260_aclkgen.xdc - ACLK-Lite signal GENERATOR output
##
## The generator drives a Manchester ACLK-Lite stream OUT of KR260 PMOD1 pin 1 =
## package H12, LVCMOS33 (the exact pin the receiver build uses as its INPUT), so the
## two boards can be wired H12 to H12 (plus a common Pmod ground) for an end-to-end
## test. Push-pull LVCMOS33, short board-to-board jumper. Verify connector positions
## against the carrier-card silkscreen (this is the generator board, not the receiver).

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports aclk_out]

## Scope-debug pins (PMOD1): frame-sync trigger + divided clk_os (MMCM alive).
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clkos_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports frame_sync_dbg]

## Asynchronous clock groups.
##
## The clk_wiz MMCM makes the ~120 MHz oversample clock (clk_out1) from pl_clk0; it
## shares pl_clk0 as a physical source but is NOT phase-comparable to the ~100 MHz
## PS clock (clk_pl_*). Declaring the domains asynchronous keeps the tool from timing
## (and falsely failing) crossings between them. The generator has no PS<->rx data
## crossing, but the MMCM-derived clock vs pl_clk0 relationship is identical to the
## receiver build, so the same constraint applies.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
