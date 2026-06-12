# deploy/tclk.md — read real TCLK events on the board

Real 3.3V biphase-mark TCLK enters H12 -> decoded -> AXI readout @ 0x8000_0000 ->
`tclk_read.py` streams events. Reuses the existing overlay (design_name=uart_echo_bd).

## 1. Build (PC)
```powershell
.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk
```
Output: `…\kria-builds\tclk\tclk.runs\impl_1\uart_echo_bd_wrapper.bit`. If Vivado
flakes on `couldn't read file` mid-BD, the wrapper retries (antivirus on C:\Xilinx).

## 2. .bit -> .bit.bin (PC, Vivado/Vitis shell)
Reuse `deploy/uart_echo.bif` (it names `uart_echo_bd_wrapper.bit`). Next to the .bit:
```bat
bootgen -arch zynqmp -process_bitstream bin -image uart_echo.bif
```

## 3. Copy to the board (over the IPv6 link-local)
```powershell
scp "$env:USERPROFILE\kria-builds\tclk\tclk.runs\impl_1\uart_echo_bd_wrapper.bit.bin" tclk_read.py "ubuntu@[fe80::48ec:6a99:b6fd:80e9%6]:~"
```

## 4. Load (board)
```bash
sudo xmutil unloadapp
sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -f Full
```

## 5. Run
```bash
ls -l /dev/uio*                       # find the readout's uioN
sudo python3 tclk_read.py /dev/uioN
```
Events scroll with codes + timestamps; a `[stats]` line prints each second.

## Wiring
Front-end drives push-pull 3.3V into **H12 (PMOD1 pin 1)**, GND to a Pmod GND pin.
Signal is ~10 MHz biphase-mark.

## Diagnosing
- Events + climbing EVT = success.
- `tclk_edges` climbing but EVT flat = signal present, decoder not locking (check
  bit framing / parity / levels).
- `tclk_edges` flat = no signal reaching the pin (front-end / wiring / pin).
- `sig_err=1` persistently = serdec sees no clean carrier.
