# vivado/ — Kria KR260 hardware build

Scripted RTL → bitstream flow for the Kria KR260 (Zynq UltraScale+ MPSoC,
part `xck26-sfvc784-2LV-c`). UltraScale+ has no open-source bitstream path, so
this stage requires **AMD Vivado** (the free **ML Standard Edition** supports the
KR260). The cocotb simulation in `tb/` stays the fast inner loop; this is the
slow path you run when you want real hardware.

## Files

| File | Role |
|------|------|
| `build.tcl` | Builds the block design (PS → pl_clk0/reset → `uart_echo_top`), then synth → impl → bitstream. **Committed.** |
| `uart_echo_bd.tcl` | Block design exported as Tcl by `build.tcl` (`write_bd_tcl`). Regenerates the BD from source control. **Committed (after first build).** |
| `build/` | Generated Vivado project, runs, and the output bitstream. **Git-ignored.** |

## Build

From the repo root (the `hw` wrappers locate Vivado and run `build.tcl` in batch):

```powershell
.\hw.ps1 build          # PowerShell
```
```bash
./hw.sh build           # git bash
```

Point the wrapper at your Vivado launcher if it isn't on PATH:
`-Vivado "C:\Xilinx\Vivado\<ver>\bin\vivado.bat"` (PS) or `export VIVADO=...` (bash).

Output: `vivado/build/uart_echo.runs/impl_1/uart_echo_bd_wrapper.bit`.

## Before first hardware use

- **Install the KR260 board file** (Vivado Store → Boards → Kria KR260). `build.tcl`
  applies the board preset when present and warns if it's missing.
- **Verify the PMOD package pins** in [`../constraints/kr260.xdc`](../constraints/kr260.xdc)
  against the official KR260 master XDC — the pins there are starter values.
- **Match the Vivado version to the board's Linux image** if you intend to use the
  `xmutil` app flow — the device-tree-overlay deployment is version-sensitive.

## Deployment

Getting the bitstream onto the board is handled separately (not scripted here).
Options: Vivado Hardware Manager over JTAG, `fpgautil -b <bit.bin> -f Full`, or the
Kria app flow (`xmutil unloadapp` / `loadapp` with a `.bit.bin` + `.dtbo` +
`shell.json` under `/lib/firmware/xilinx/<app>/`).
