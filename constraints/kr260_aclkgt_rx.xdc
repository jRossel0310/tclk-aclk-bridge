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

## Async clock groups: ALL PL-domain clocks vs ALL GT-domain clocks.
## The PS<->GT data path crosses only through the async_fifo in aclk_readout_core
## (Gray-counter CDC-safe) plus the buffer-bypass reset 2-FF sync, so timing across
## these domains is intentionally cut.
##  - PL group: pl_clk0 AND its generated children (the clk_wiz 50 MHz freerun).
##  - GT group: gt_refclk-derived clocks (TX path) AND the data-RECOVERED RX clocks
##    (rxoutclkpcs / gtwiz_userclk_rx_srcclk + their usrclk2 dividers). With RX buffer
##    bypass the RX user logic runs on the recovered clock, which is NOT generated from
##    gt_refclk, so it must be named explicitly here (the old gt_refclk-only group
##    missed it, leaving the FIFO CDC timed -> false setup violations).
set_clock_groups -name async_pl_vs_gt -asynchronous \
    -group [get_clocks -include_generated_clocks clk_pl_0] \
    -group [get_clocks -include_generated_clocks \
                {gt_refclk gtwiz_userclk_rx_srcclk_out* gtwiz_userclk_tx_srcclk_out* rxoutclk*}]
