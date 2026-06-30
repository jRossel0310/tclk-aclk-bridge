## constraints/kr260_aclk_pipeline.xdc - INTEGRATED TCLK -> ACLK pipeline (one board).
##
## Union of the TCLK (kr260_tclk.xdc) and GT/SFP (kr260_aclkgt_rx.xdc) constraints,
## plus the ACLK-Lite mirror output, plus the widened async clock groups this design
## needs (it has MORE async clock pairs than any prior single build).
##
## Port names match the BD external ports created in build_aclk_pipeline.tcl (the
## make_wrapper top preserves them: tclk, gt_*, sfp_*, aclk_lite_out, dbg_hb).

## ---------------------------------------------------------------------------
## Pin LOCs
## ---------------------------------------------------------------------------

## TCLK biphase-mark input: KR260 PMOD1 pin 1 = package H12, LVCMOS33.
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports tclk]

## GT serial + reference clock (Bank 224). No IOSTANDARD on GT dedicated pins
## (differential refclk / MGT serial I/O do not take an IOSTANDARD - the tool errors).
##   GT TX serial:  MGTHTXP/N2_224 on R4/R3 (re-encoded ACLK out -> SFP TX).
##   GT RX serial:  MGTHRXP/N2_224 on T2/T1 (real SFP+ data in).
##   GT refclk:     MGTREFCLK0_224 differential pair on Y6/Y5 (156.25 MHz).
set_property PACKAGE_PIN R4 [get_ports gt_txp]
set_property PACKAGE_PIN R3 [get_ports gt_txn]
set_property PACKAGE_PIN T2 [get_ports gt_rxp]
set_property PACKAGE_PIN T1 [get_ports gt_rxn]
set_property PACKAGE_PIN Y6 [get_ports gt_refclk_p]
set_property PACKAGE_PIN Y5 [get_ports gt_refclk_n]

## 156.25 MHz refclk period = 6.400 ns
create_clock -period 6.400 -name gt_refclk [get_ports gt_refclk_p]

## SFP+ sideband control/status (KR260 carrier PL I/O, LVCMOS33). sfp_tx_disable is the
## load-bearing one: a wrong LOC leaves the laser off (held high/floating = laser OFF).
set_property -dict {PACKAGE_PIN Y10 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8} [get_ports sfp_tx_disable]
set_property -dict {PACKAGE_PIN A10 IOSTANDARD LVCMOS33} [get_ports sfp_tx_fault]
set_property -dict {PACKAGE_PIN J12 IOSTANDARD LVCMOS33} [get_ports sfp_rx_los]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVCMOS33} [get_ports sfp_mod_abs]

## ACLK-Lite biphase-mark mirror output: Pmod pin = package B10, LVCMOS33.
set_property -dict {PACKAGE_PIN B10 IOSTANDARD LVCMOS33} [get_ports aclk_lite_out]

## Debug heartbeat (readout #1 liveness) on PMOD1 pin 7 = package D11, LVCMOS33.
## Every top-level port needs a LOC + IOSTANDARD or write_bitstream fails DRC (NSTD-1/UCIO-1).
set_property -dict {PACKAGE_PIN D11 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## ---------------------------------------------------------------------------
## Asynchronous clock groups
## ---------------------------------------------------------------------------
## This design has THREE physically-unrelated clock families. Putting each in its own
## -group makes all three mutually asynchronous, which cuts EVERY cross-family CDC path
## in one stanza. Without these the tool TIMES the (CDC-safe) crossings, reports huge
## false-negative slack, and the router burns iterations chasing violations that aren't
## real. Every cross-family path here goes through a CDC-safe structure (async FIFO with
## Gray pointers, gray-code timebase counter, or 2-FF synchronizer), so cutting is safe.
##
##   Group A (PS/AXI):  clk_pl_0  - the ~100 MHz PS clock + all generated children
##                      (pl_clk0). This is s_axi_aclk and the SmartConnect clock.
##   Group B (MMCM):    clk_out*_clk_wiz_*  - ALL clk_wiz outputs:
##                        clk_wiz_0   -> clk_out1 = clk_80m, clk_out2 = clk_40m
##                        clk_wiz_freerun -> clk_out1 = freerun_50
##                      (80 and 40 are 2:1 phase-related from one MMCM and cross each
##                      other cleanly, so they stay in ONE group.)
##   Group C (GT):      gt_refclk + the GT user/recovered clocks rx_usrclk2 / tx_usrclk2
##                      (gtwiz_userclk_*_srcclk_out* and their dividers) + rxoutclk*.
##
## Which CDC each cross-group cut covers:
##   A<->B : pl_clk0 <-> clk_40m / clk_80m   - global_timebase gray CDC (ref pl_clk0 ->
##           ts_a in clk_40m) AND the AXI s_axi_aclk <-> TCLK-readout async FIFO.
##   A<->C : pl_clk0 <-> rx_usrclk2 / tx_usrclk2 - global_timebase gray CDC (ref pl_clk0
##           -> ts_b in rx_usrclk2), the ACLK-readout async FIFO (rx_usrclk2 <-> AXI),
##           and the GT-health cdc_gray_count / 2-FF status syncs into s_axi_aclk.
##   B<->C : clk_40m / clk_80m <-> rx_usrclk2 / tx_usrclk2 - aclk_lite_bridge async FIFO
##           (rx_usrclk2 -> clk_80m) AND aclk_tclk_encoder's clk_40m -> tx_usrclk2 handoff.
##
## NOTE: clk_pl_0 and gt_refclk are PRIMARY clocks here, so -include_generated_clocks
## pulls in their MMCM/GT-derived children; the clk_out* wildcard captures the clk_wiz
## generated clocks by name (they are not reached by -include_generated_clocks off a port).
set_clock_groups -name async_pipeline -asynchronous \
    -group [get_clocks -include_generated_clocks clk_pl_0] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}] \
    -group [get_clocks -include_generated_clocks \
                {gt_refclk gtwiz_userclk_rx_srcclk_out* gtwiz_userclk_tx_srcclk_out* rxoutclk*}]
