## constraints/kr260_aclkgt_rx.xdc - Milestone 1: RECEIVER (real SFP RX, no loopback).
##
## GT reference clock: MGTREFCLK0_224 differential pair on Y6/Y5.
## GT RX serial:       MGTHRXP/N2_224 on T2/T1 (real SFP+ data in).
## GT TX serial:       MGTHTXP/N2_224 on R4/R3 (idle out; the GT is duplex).
## No IOSTANDARD on GT dedicated pins (differential refclk / MGT serial I/O do not
## take an IOSTANDARD constraint - the tool errors if one is set).

set_property PACKAGE_PIN Y6 [get_ports gt_refclk_p]
set_property PACKAGE_PIN Y5 [get_ports gt_refclk_n]
set_property PACKAGE_PIN T2 [get_ports gt_rxp]
set_property PACKAGE_PIN T1 [get_ports gt_rxn]
set_property PACKAGE_PIN R4 [get_ports gt_txp]
set_property PACKAGE_PIN R3 [get_ports gt_txn]

## 156.25 MHz refclk period = 6.400 ns
create_clock -period 6.400 -name gt_refclk [get_ports gt_refclk_p]

## Debug heartbeat to Pmod (PMOD1 pin 1 = H12, LVCMOS33).
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Async clock groups: GT recovered/user clocks + freerun vs PS/AXI (pl_clk0).
## The PS<->GT data path crosses only through the async_fifo in aclk_readout_core
## (Gray-counter CDC-safe). Skip timing across these domains.
set_clock_groups -name async_ps_vs_gt -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -include_generated_clocks gt_refclk]
