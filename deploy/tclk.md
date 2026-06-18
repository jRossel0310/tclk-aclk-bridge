# deploy/tclk.md — read real TCLK events on the board

Real 3.3V biphase-mark TCLK enters H12 -> decoded -> AXI readout @ 0x8000_0000 ->
`tclk_read.py` streams events. Reuses the existing overlay (design_name=uart_echo_bd).

## 1. Build + package (PC)
```powershell
.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk
```
One command now does Vivado + bootgen + hashing. Output (repo-local):
`build\kria\tclk\tclk.runs\impl_1\uart_echo_bd_wrapper.bit(.bin)`. It prints:
- `BIT` path, `BIN` path
- `MD5` (and `SHA256`) — also saved in `build\kria\tclk\build-manifest.json`

If Vivado flakes on `couldn't read file` mid-BD, the wrapper retries (antivirus
on C:\Xilinx). If bootgen is not found, add Vivado 2024.2's `bin` to PATH.

## 2. Copy to the board (PC)
```powershell
.\hw.ps1 deploy -Name tclk -DeployHost "ubuntu@[fe80::48ec:6a99:b6fd:80e9%6]"
```
Copies `uart_echo_bd_wrapper.bit.bin` + `tclk_read.py` + `tclk_filter.py` to `~`.
(Manual equivalent: `scp` those three files yourself.)

## 3. Load (board) — UIO + overlay
```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin      # must match the PC MD5
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
ls -l /dev/uio*                            # find the readout's uioN
```
The `-o ...dtbo` overlay is required: it creates `/dev/uioN` and releases PL
reset. Do NOT use `-f Full` here (no UIO device is created). The cosmetic
`OF: overlay: WARNING: memory leak will occur ...` on load is harmless.

## 4. Run
```bash
cat /sys/class/uio/uio*/name              # confirm which uioN is the readout
sudo python3 -u tclk_read.py /dev/uio4 --drop 07,0F,BA,8F
```
Run with `-u` for unbuffered output. Events scroll with codes + timestamps; a
`[stats]` line prints each second. If UIO is locked down, the reader can fall
back to `/dev/mem` at `0x8000_0000` (see deploy/README.md).

## Wiring
Front-end drives push-pull 3.3V into **H12 (PMOD1 pin 1)**, GND to a Pmod GND pin.
Signal is ~10 MHz biphase-mark.

## Diagnosing
- Events + climbing EVT = success.
- `tclk_edges` climbing but EVT flat = signal present, decoder not locking (check
  bit framing / parity / levels).
- `tclk_edges` flat = no signal reaching the pin (front-end / wiring / pin).
- `sig_err=1` persistently = serdec sees no clean carrier.
