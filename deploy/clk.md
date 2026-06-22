# Unified ACLK/TCLK readout on the KR260

One bitstream that decodes BOTH real TCLK and real ACLK-Lite from a single H12 input
(serdec4_9MHz + clk_byte_framer -> shared readout). The shipped tclk/aclk builds are
unchanged; this is the unified target.

## Build (PC, Vivado 2024.2)

    .\hw.ps1 build -Tcl vivado\build_clk.tcl -Name clk

Produces `build/kria/clk/clk.runs/impl_1/uart_echo_bd_wrapper.bit.bin` (+ MD5).

## Deploy

    .\hw.ps1 deploy -Name clk -DeployHost ubuntu@<host>

Copies the bin, `clk_read.py`, and `tclk_filter.py` to ~ on the board.

## Load + read

    md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC-side MD5
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
    sudo python3 -u clk_read.py /dev/uio4

Each decoded event prints `ts_ticks dt_us event data tclk has_data`. Plug the real
office TCLK into H12 -> 8-bit events (tclk=1) matching the TCLK event table; plug the
generator (after it is reflashed to the real framing) -> 16-bit ACLK events + 64-bit
data. `--drop 07,0F` suppresses event codes. line_edges climbs whenever H12 toggles.

## Input

Same H12 pin (PMOD1 pin 1, LVCMOS33) as the TCLK build; swap the cable between the real
TCLK source and the generator. Both must be the real ISD Manchester framing; the decoder
auto-detects frame length (1 byte = TCLK, 2 = ACLK event, 12 = event + data).
