# Gigabit ACLK over GT/SFP readout on the KR260

The gigabit-ACLK receiver: the inherited `ACLK_RCV` 8b/10b decoder fed by a GTH
transceiver on the KR260 SFP+ cage, into the shared timestamp/FIFO/AXI readout (same
register map as the TCLK/ACLK-Lite builds). Distinct from the ACLK-Lite-over-H12-pin
`aclk` build.

Three staged build targets share `design_name = uart_echo_bd` (so the overlay/bitstream
name and the UIO path are identical across them):
- **`aclkgt_loop`** (Milestone 0): single board, GT in near-end PMA loopback, fed by the
  on-board `aclk_gt_frame_gen`. Proves the whole GT->decode->readout chain with NO optics.
  HARDWARE-CONFIRMED working (the 3 generator events decode).
- **`aclkgt_rx`** (Milestone 1): receiver, real SFP RX (loopback off) -> decode -> PS.
- **`aclkgt_gen`** (Milestone 2): generator, real SFP TX (pure transmitter).

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

## Milestones 1 + 2: two-board link over real SFP+ fiber

Two KR260s, SFP+ to SFP+ over fiber. Board B transmits the gigabit-ACLK stream; board A
receives and decodes it to the PS. Both boards share the identical 156.25 MHz carrier
refclk, so they lock at 1.25 Gbps with no shared reference.

### Build

    .\hw.ps1 build -Tcl vivado\build_aclkgt_rx.tcl  -Name aclkgt_rx     # board A (receiver)
    .\hw.ps1 build -Tcl vivado\build_aclkgt_gen.tcl -Name aclkgt_gen    # board B (generator)

### Wire it up

- Plug a matched-wavelength SFP+ optical module into BOTH boards' SFP+ cages.
- Connect a duplex LC fiber: board B TX -> board A RX (the data path). The reverse strand
  (board A TX -> board B RX) carries idle and is unused, but a duplex patch connects both.
- If the generator's SFP shows no light / board A never sees a signal, check for an SFP
  TX_DISABLE line on the cage (must be low to enable the optical TX); the KR260 community
  XDCs do not list one, but verify against your carrier rev (see docs/aclkgt-hardware-facts.md).

### Board B (generator) - flash, no reader

    scp build/kria/aclkgt_gen/aclkgt_gen.runs/impl_1/uart_echo_bd_wrapper.bit.bin ubuntu@<boardB>:~
    # on board B:
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo

Board B is a pure transmitter (no PS readout). `dbg_hb` (PMOD1 pin1 / H12) toggles slowly
while the generator emits frames - a scope/LED sanity check that it is alive.

### Board A (receiver) - flash + read

    scp build/kria/aclkgt_rx/aclkgt_rx.runs/impl_1/uart_echo_bd_wrapper.bit.bin deploy/aclkgt_read.py deploy/tclk_filter.py ubuntu@<boardA>:~
    # on board A:
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
    sudo python3 -u aclkgt_read.py /dev/uio4

### Expected on board A

The receiver's `[stats]` GT-health word climbs into lock once the fiber + generator are up:
`commadet` rising (commas arriving), `commaever=1`, `byteali=1`, `rcv_aligned=1`, and the
three generator events (0x0001 / 0x00A5 / 0x1000 + their 64-bit data) printing below, with
`EVT` climbing and `ERR=0`.

As in M0, the free-running generator (~6M frames/s) outruns the PS reader, so you see a
sparse, correctly-decoded sample (out-of-cycle order). Decode is exact; the rate is just the
reader/FIFO limit. For a clean in-order stream add an inter-frame gap to `aclk_gt_frame_gen`
(its rate then matches a realistic ACLK and the reader keeps up).

### Receiver DEBUG (0xA0) decode (the `[stats]` line)
`{ rcv_aligned[31], byteali[30], comma_seen[29], commadet_count[28:0] }`. If no events:
`commadet=0` -> no signal/comma at the RX (fiber orientation, SFP TX_DISABLE, wavelength,
or refclk); `commadet` climbing but `rcv_aligned=0` -> aligning but CRC failing (rare; check
the two boards run the same GT line rate / IP).
