## constraints/kr260_pinblink.xdc - H12 pin-toggle bring-up test
##
## Drives a 0.5 Hz square wave (rtl/pin_blink.v) on KR260 PMOD1 pin 1 = package
## pin H12, LVCMOS33, so the physical pin can be verified at 3.3V BEFORE the full
## TCLK design is wired up. This is the pin chosen for the TCLK input.
##
## NOTE: the pin-number-to-connector-position mapping differs across sources, so
## confirm where H12 lands on the carrier-card silkscreen / schematic before
## probing. The package pin itself (H12, LVCMOS33) is correct.

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports pin_h12]
