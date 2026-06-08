## constraints/kr260.xdc — Kria KR260, uart_echo AXI-loopback build
##
## This design tests uart_echo over the network: the PS reaches an AXI UART Lite,
## whose serial tx/rx are cross-wired to uart_echo INSIDE the PL. There are NO
## PL-external user pins — `serial_in`/`serial_out` are internal nets now, and the
## PS DDR/MIO/FIXED_IO are constrained automatically by the KR260 board preset.
## So this file is intentionally (almost) empty.
##
## If you ever switch back to the PMOD build (serial lines on real pins), restore
## the package-pin assignments here, e.g. (KR260 PMOD1 candidates from the master
## pinout: pin1=H12, pin2=E10, pin3=D10, pin4=C11, pin5=B10, pin6=E12, pin7=D11,
## pin8=B11 — all LVCMOS33):
##   set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports serial_in]
##   set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports serial_out]
