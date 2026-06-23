# Gigabit ACLK over GT/SFP readout on the KR260

The gigabit-ACLK receiver: the inherited `ACLK_RCV` 8b/10b decoder fed by a GTH
transceiver on the KR260 SFP+ cage, into the shared timestamp/FIFO/AXI readout (same
register map as the TCLK/ACLK-Lite builds). Distinct from the ACLK-Lite-over-H12-pin
`aclk` build.

Three staged build targets share `design_name = uart_echo_bd` (so the overlay/bitstream
name and the UIO path are identical across them):
- **`aclkgt_loop`** (Milestone 0): single board, GT in near-end PMA loopback, fed by the
  on-board `aclk_gt_frame_gen`. Proves the whole GT->decode->readout chain with NO optics.
- `aclkgt_rx` (Milestone 1): receiver, real SFP RX (loopback off). [not built yet]
- `aclkgt_gen` (Milestone 2): generator, real SFP TX. [not built yet]

## Milestone 0: single-board GT loopback (no optics needed)

### Build (PC, Vivado 2024.2)

    .\hw.ps1 build -Tcl vivado\build_aclkgt_loop.tcl -Name aclkgt_loop

Produces `build/kria/aclkgt_loop/aclkgt_loop.runs/impl_1/uart_echo_bd_wrapper.bit.bin`
(md5 in the build output + `build-manifest.json`). Timing met (routed WNS +4.7 ns).

### Get the bitstream + reader onto the board

    .\hw.ps1 deploy -Name aclkgt_loop -DeployHost ubuntu@<board>
    # then make sure the GT reader + filter helper are on the board too:
    scp deploy/aclkgt_read.py deploy/tclk_filter.py ubuntu@<board>:~

### Load (on the board)

    md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC-side MD5
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo

### Read events

    sudo python3 -u aclkgt_read.py /dev/uio4

(Same LPD base 0x8000_0000 and overlay as the other builds, so the UIO node is the same
as your tclk/aclk bring-ups - typically /dev/uio4; the reader's startup probe confirms it.)

### Expected (loopback is the on-board generator's compiled-in timeline)

The generator cycles three events forever:

| event | data (64-bit) |
|---|---|
| 0x0001 | 0x1111222233334444 |
| 0x00A5 | 0xAAAABBBBCCCCDDDD |
| 0x1000 | 0x0123456789ABCDEF |

So a healthy M0 shows, in the reader:
- the three events repeating in order, each with `has_data=1` and its 64-bit payload,
  `tclk=0`;
- `EVENT_COUNT` climbing steadily, `ERROR_COUNT` flat at 0 (after the GT aligns),
  `NULL_COUNT` 0;
- `LOCK` bit0 = 1 (here it reflects GT `tx_done & rx_done`, the link-ready proxy, not an
  MMCM);
- the DEBUG word's frame counter climbing (decoded-frame activity).

### Input / clocking notes

- **No external input.** The GT loops TX->RX internally (near-end PMA, `loopback_in=010`).
  The SFP TX pins (R4/R3) still transmit but connect to nothing in M0; the SFP RX is tied
  off. No SFP module or fiber is required.
- GT refclk = 156.25 MHz on Y6/Y5 (MGTREFCLK0_224), free-running from the carrier (Si5332
  on revA / discrete osc on revB). The GT reset FSM runs on a 50 MHz freerun clock the BD
  derives from pl_clk0.
- Line rate 1.25 Gbps, 8b/10b, 16-bit user data; the recovered RX user clock (timestamp
  domain) is 62.5 MHz, so the reader's `TICK_NS = 16.0`.
- If `LOCK` never reaches 1 or `ERROR_COUNT` keeps climbing: confirm the carrier refclk is
  live on Y6/Y5 (the GT cannot lock without it), then board revision; the PL decode/readout
  chain itself is already sim-proven, so a loopback failure points at the GT/refclk, not the
  readout.
