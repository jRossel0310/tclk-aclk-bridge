## constraints/kr260.xdc — Kria KR260, uart_echo_top bring-up
##
## Maps the two top-level UART pins to PMOD package pins. The Zynq UltraScale+
## PS supplies the PL clock/reset inside the block design, so:
##   - there is NO physical clock pin here; `clk` comes from pl_clk0 and Vivado
##     derives that clock from the block design (no create_clock needed).
##   - only the asynchronous UART pad timing exceptions live in this file.
##
## !!! VERIFY THE PACKAGE PINS BELOW !!!
## These are starter values from the KR260 master pinout (PMOD candidates:
## H12 B10 E10 E12 D10 D11 C11 B11). Confirm against the official KR260 master
## XDC / carrier-card schematic from the AMD Kria K26 docs before wiring an
## adapter to the board — a wrong pin can drive a bank at the wrong I/O standard.

## --- UART I/O on PMOD (LVCMOS33 = 3.3 V PMOD bank) ------------------------
set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports serial_in]
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports serial_out]

## --- Timing exceptions for the slow, asynchronous UART pads --------------
## serial_in is resampled by the on-chip 2-stage synchronizer, and UART symbols
## are thousands of clk cycles long, so a tight pad-to-register timing closure is
## neither achievable nor meaningful. Cut these paths from timing analysis.
set_false_path -from [get_ports serial_in]
set_false_path -to   [get_ports serial_out]

## --- External adapter wiring (reference) ---------------------------------
##   FTDI/USB-UART (3.3 V TTL)      KR260 PMOD
##   ------------------------       ----------
##   TX  ----------------------->   serial_in  pin
##   RX  <-----------------------   serial_out pin
##   GND ----------------------->   GND
## Open a serial terminal at the design baud (115200 8-N-1); typed characters
## should echo back.
