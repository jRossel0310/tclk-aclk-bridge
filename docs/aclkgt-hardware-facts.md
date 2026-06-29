# KR260 SFP / GTH hardware facts for the gigabit-ACLK build

Captured 2026-06-23 (Task 1 of the aclkgt plan). Cross-verified across three
independent open-source KR260 projects (Corundum NIC, taxi/verilog-ethernet, and the
Hackster Aurora-64B/66B-on-KR260 guide) plus AMD DS987/UG1091. The official AMD
carrier schematic (XTP743) is NDA-gated, but the three community XDCs agree exactly.

## Build-against values (what the GT IP / XDC use now)

| Field | Value | Status |
|---|---|---|
| GT type | GTH (UltraScale+ GTHE4) | CONFIRMED (K26 sfvc784 has one bonded GTH quad) |
| GT bank / quad | Bank 224; Vivado GT-Wizard UI "Quad X0Y1" | CONFIRMED |
| GT lane | Vivado UI "Lane X0Y6" = LOC `GTHE4_CHANNEL_X1Y12` (lane 2 of bank 224) | CONFIRMED |
| SFP TX_P / TX_N | **R4 / R3** (`MGTHTXP2_224` / `MGTHTXN2_224`) | CONFIRMED (3 sources) |
| SFP RX_P / RX_N | **T2 / T1** (`MGTHRXP2_224` / `MGTHRXN2_224`) | CONFIRMED (3 sources) |
| REFCLK_P / REFCLK_N | **Y6 / Y5** (`MGTREFCLK0P_224` / `MGTREFCLK0N_224`) | CONFIRMED (3 sources) |
| REFCLK frequency | **156.25 MHz** (period 6.400 ns) | CONFIRMED (DTS clk_156 = 156250000; XDC 6.400) |
| REFCLK source | revA: Si5332 (U17, factory-programmed NVM); revB: discrete oscillator (U90) | CONFIRMED |
| REFCLK free-running at power-up | **YES** - no software/I2C init needed (both revs) | CONFIRMED |
| Vivado board-file SFP preset | NONE - SFP GTH path is not in the board file; constrain by hand in XDC | CONFIRMED |
| **Chosen line rate** | **1.25 Gbps** (= 156.25 MHz x 8; 8b/10b; 16-bit user @ 62.5 MHz usrclk2) | DECISION (see below) |
| Encoding | 8b/10b, comma `0xBC` (K28.5), 16-bit user data + 2-bit K | DESIGN (matches ACLK_RCV) |

LOC primitive cross-reference (for the XDC, if pin LOC alone is ambiguous):
`GTHE4_COMMON_X1Y3` (quad), `GTHE4_CHANNEL_X1Y12` (the SFP lane).

## Line-rate decision (vs the spec's "match real ACLK exactly")

The spec chose "match the real ACLK line rate exactly" so the same RX bitstream later
works on a live Fermilab fiber. The real rate is still unconfirmed (only inferable as
~1.2 Gbps from Evan's 60 MHz x 16-bit x 10/8). For the **two-board generator+receiver
link**, both boards are KR260s sharing the identical 156.25 MHz carrier refclk, so they
lock to whatever rate we configure regardless of the real ACLK rate. We therefore build
the two-board milestones at **1.25 Gbps** - the clean 156.25 x 8 SerDes rate (16-bit
user @ 62.5 MHz, ~Evan's 60 MHz), well supported by the GTH. Reconciling to the exact
real ACLK rate is deferred to the eventual real-fiber RX step (re-tune `TX/RX_LINE_RATE`
in the GT IP once the real rate is confirmed; nothing else changes).

## Ready-to-use XDC block (community-verified, hardware-proven on KR260)

```tcl
# KR260 SFP+ GTH - Bank 224 / GTHE4_CHANNEL_X1Y12. VERIFY against your carrier rev.
set_property PACKAGE_PIN R4 [get_ports gt_txp]            ;# MGTHTXP2_224
set_property PACKAGE_PIN R3 [get_ports gt_txn]            ;# MGTHTXN2_224
set_property PACKAGE_PIN T2 [get_ports gt_rxp]            ;# MGTHRXP2_224
set_property PACKAGE_PIN T1 [get_ports gt_rxn]            ;# MGTHRXN2_224
set_property PACKAGE_PIN Y6 [get_ports gt_refclk_p]       ;# MGTREFCLK0P_224
set_property PACKAGE_PIN Y5 [get_ports gt_refclk_n]       ;# MGTREFCLK0N_224
create_clock -period 6.400 -name gt_refclk [get_ports gt_refclk_p]

# SFP+ sideband (PL I/O, LVCMOS33). TX_DISABLE MUST be driven LOW to enable the laser - the GT
# does not control it, and a floating/high pin = laser OFF (this was the root cause of the dead
# link). The 3 status pins are monitor-only. SFP I2C (SCL=AB11, SDA=AC11) is on PL fabric (not
# the PS I2C bus), so DDM readout needs a PL AXI-IIC core.
set_property -dict {PACKAGE_PIN Y10 IOSTANDARD LVCMOS33 SLEW SLOW DRIVE 8} [get_ports sfp_tx_disable]
set_property -dict {PACKAGE_PIN A10 IOSTANDARD LVCMOS33} [get_ports sfp_tx_fault]
set_property -dict {PACKAGE_PIN J12 IOSTANDARD LVCMOS33} [get_ports sfp_rx_los]
set_property -dict {PACKAGE_PIN W10 IOSTANDARD LVCMOS33} [get_ports sfp_mod_abs]
```

## GT Wizard config (gtwizard_ultrascale, raw - NOT Aurora)

- Transceiver type GTH, Quad X0Y1, Lane X0Y6.
- TX/RX line rate 1.25 Gbps; TX/RX refclk 156.25 MHz; refclk source = MGTREFCLK0 of the quad (Y6/Y5).
- 8b/10b encode (TX) + decode (RX); 16-bit user data width; 2-bit K per 16-bit word.
- RX comma detect + plus/minus align on `0xBC` (K28.5); comma value `0101111100` (P) / `1010000011` (M).
- Include "Shared Logic in core" (so the IP brings its own QPLL/usrclk helper); freerun clock = PS pl_clk0 (~100 MHz).
- Near-end PMA loopback available via `loopback_in` (3'b010 for M0; 3'b000 for the real SFP M1/M2).

## To verify on the board at bring-up

1. Board revision (label on the carrier): revA (Si5332) vs revB (discrete osc). Pins and 156.25 MHz are identical either way.
2. Confirm 156.25 MHz is live on the GT refclk before trusting GT lock: the GT reset-done / cdr-stable outputs going high (surfaced in the readout DEBUG/LOCK) is the in-design check; a scope on the SOM240_2 refclk test point is the external check.
3. Only one SFP+ cage on the KR260 - no TX/RX-disable GPIO is in the community XDCs, but if the SFP module will not transmit, check for an SFP TX_DISABLE line on the cage (pull low to enable) and the module's presence/LOS pins.

## Tooling

Vivado 2024.2 confirmed on PATH (`C:\Xilinx\Vivado\2024.2\bin\vivado.bat`) - same as the repo's existing builds. `.\hw.ps1 build -Tcl ... -Name ...` can run here.

## Still UNCONFIRMED (non-blocking for the two-board link)

- The real Fermilab gigabit-ACLK line rate (needed only to later receive a live fiber unchanged). Sources: Fermilab/Evan, or the original `gtwizard_ultrascale_0` `.xci` from Evan's bring-up board if recoverable.
- Whether MGTREFCLK0 (vs MGTREFCLK1) is the exact wired input - the community XDCs use MGTREFCLK0P_224 = Y6, which is the build-against assumption; the GT IP refclk-source dropdown must match (CLK0).
