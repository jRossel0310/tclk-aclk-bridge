# ACLK-Lite readout on the KR260

The ACLK-Lite (Manchester ADM) readout. Same shared timestamp/FIFO/AXI readout as
the TCLK build, just fed by the Manchester decoder. The TCLK build (deploy/tclk.md)
is unchanged; this is the parallel ACLK target.

## Build (PC, Vivado 2024.2)

    .\hw.ps1 build -Tcl vivado\build_aclk.tcl -Name aclk

Produces `build/kria/aclk/aclk.runs/impl_1/uart_echo_bd_wrapper.bit.bin` (+ MD5 in the
build output and `build-manifest.json`). design_name=uart_echo_bd, so the bitstream
name and the overlay are identical to the TCLK build.

## Deploy (scp the bin + readers)

    .\hw.ps1 deploy -Name aclk -DeployHost ubuntu@kria

Copies `uart_echo_bd_wrapper.bit.bin`, `aclk_read.py`, and `tclk_filter.py` to ~ on the
board.

## Load (on the board)

    md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC-side MD5
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo

## Read events

    sudo python3 -u aclk_read.py /dev/uio4

The startup probe reports MMCM lock + a heartbeat trust check. Then each decoded event
prints `ts_ticks dt_us event data tclk has_data` (event is the full 16-bit id; data is
the 64-bit payload when has_data=1; tclk=1 marks a legacy 8-bit event). Drop events with
`--drop 09D,016F` (hex codes). line_edges in the stats line climbs whenever the H12
Manchester line toggles.

## Input

The ACLK-Lite Manchester line enters on H12 (PMOD1 pin 1, LVCMOS33), the same pin the
TCLK build uses (ACLK-Lite rides the same TTL interface). OVERSAMPLE=12 and the 120 MHz
oversample clock assume a ~10 MHz line bit rate; both are tunable in
`rtl/aclk_readout_bd_top.v` / `vivado/build_aclk.tcl` and must be reconciled against a
live source. No real ACLK-Lite transmitter is wired yet, so end-to-end decode is
verified once a source is connected; until then the build, AXI bus, clocking, and
line-activity diagnostic are the verifiable parts.
