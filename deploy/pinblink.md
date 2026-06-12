# deploy/pinblink.md — verify the TCLK pin (H12) at 3.3V on the board

Goal: prove the chosen TCLK input pin (**PMOD1 pin 1 = package `H12`, LVCMOS33**)
physically toggles at 3.3V, *before* building the full TCLK design. The PL design
(`vivado/build_pinblink.tcl`) drives H12 as a **0.5 Hz square wave** (1 s high,
1 s low) with `rtl/pin_blink.v`. It also keeps the proven AXI-GPIO heartbeat, so
the existing overlay loads unchanged and `pltest.py` still works.

> A package pin can only be driven by the PL fabric, so this needs a bitstream
> (there is no PS/MIO shortcut to H12). This is the smallest possible such design.

## 1. Build the bitstream (PC)

```powershell
.\hw.ps1 build -Tcl vivado\build_pinblink.tcl -Name pinblink
```
Output: `…\kria-builds\pinblink\pinblink.runs\impl_1\uart_echo_bd_wrapper.bit`
(the wrapper is named `uart_echo_bd_wrapper` on purpose — it reuses the existing
overlay, so you only swap the `.bit.bin`). If Vivado flakes on `couldn't read
file …` mid-block-design, the wrapper just retries (antivirus on `C:\Xilinx`).

## 2. Convert .bit → .bit.bin (PC, in a Vivado/Vitis shell)

The existing `deploy/uart_echo.bif` already names `uart_echo_bd_wrapper.bit`, so
reuse it. Copy it next to the `.bit`, then:
```bat
bootgen -arch zynqmp -process_bitstream bin -image uart_echo.bif
```
→ `uart_echo_bd_wrapper.bit.bin`

## 3. Copy to the board

```bash
scp uart_echo_bd_wrapper.bit.bin pltest.py ubuntu@<board-ip>:~
```

## 4. Load it (on the board, over SSH)

```bash
sudo xmutil unloadapp                                   # free the PL
sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -f Full   # program the PL
```

## 5. Measure H12

Probe **PMOD1 pin 1** with a DMM (DC volts) or scope, ground referenced to a PMOD
GND pin:
- DMM: reading alternates between ~0 V and ~3.3 V once per second.
- Scope/LED: a clean 0.5 Hz square wave swinging 0 ↔ 3.3 V.

That confirms the pin, the LVCMOS33 bank, and the carrier level-translator all
pass a 3.3V signal on H12.

> Confirm which physical connector position is "pin 1" against the carrier-card
> silkscreen / schematic — the package pin (H12) is correct, but pin-number-to-
> position numbering differs across references.

## 6. (optional) Confirm the PL is configured + clocked

```bash
sudo python3 pltest.py /dev/uioN     # find N via: ls -l /dev/uio*
```
The heartbeat counter increments → the PL is alive (same check as the pltest
design). This is independent of the H12 measurement.

---

This test drives H12 as an **output**. The real TCLK use is an **input**; the
auto-direction level translator handles both, but for the input path make sure
your front-end drives push-pull 3.3V CMOS (see the `kr260-pmod-pinout` notes).
