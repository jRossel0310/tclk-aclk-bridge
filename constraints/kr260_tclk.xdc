## constraints/kr260_tclk.xdc - real TCLK input
##
## The biphase-mark TCLK line (3.3V baseband from the external front-end) enters
## on KR260 PMOD1 pin 1 = package H12, LVCMOS33. Verify the physical connector
## position against the carrier-card silkscreen; the package pin (H12) is correct.

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports tclk]
