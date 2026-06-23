## constraints/kr260_aclkgt.xdc - Milestone 0: single-board GT near-end-PMA loopback.
##
## GT reference clock: MGTREFCLK0_224 differential pair on Y6/Y5.
## GT TX serial:       MGTHTXP/N2_224 on R4/R3. (TX only; loopback ties RX internally.)
## No IOSTANDARD on GT dedicated pins (differential refclk / MGT serial I/O do not
## take an IOSTANDARD constraint - the tool will error if one is set).

set_property PACKAGE_PIN Y6 [get_ports gt_refclk_p]
set_property PACKAGE_PIN Y5 [get_ports gt_refclk_n]
set_property PACKAGE_PIN R4 [get_ports gt_txp]
set_property PACKAGE_PIN R3 [get_ports gt_txn]

## 156.25 MHz refclk period = 6.400 ns
create_clock -period 6.400 -name gt_refclk [get_ports gt_refclk_p]

## Debug heartbeat to Pmod (PMOD1 pin 1 = H12, LVCMOS33). This pin is shared with
## the ACLK receiver build's input, but only one bitstream is loaded at a time.
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Async clock groups: GT recovered/user clocks + freerun vs PS/AXI (pl_clk0).
## The PS<->GT data path crosses only through the async_fifo in aclk_readout_core,
## which is explicitly CDC-safe (Gray counter). Tell the tool to skip timing across
## these domains.
set_clock_groups -name async_ps_vs_gt -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -include_generated_clocks gt_refclk]
