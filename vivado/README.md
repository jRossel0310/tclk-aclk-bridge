# vivado/ - Kria KR260 hardware build

Scripted RTL → bitstream flow for the Kria KR260 (Zynq UltraScale+ MPSoC,
part `xck26-sfvc784-2LV-c`). UltraScale+ has no open-source bitstream path, so
this stage requires **AMD Vivado** (the free **ML Standard Edition** supports the
KR260). The cocotb simulation in `tb/` stays the fast inner loop; this is the
slow path you run when you want real hardware.

## Files

| File | Role |
|------|------|
| `build_aclk_pipeline.tcl` | Builds the single-board TCLK -> ACLK pipeline block design, then synth -> impl -> bitstream. **Committed.** |
| `ip/aclkgt_gt/`, `ip/gen_aclkgt_gt.tcl` | Committed GT transceiver IP (GTH, 1.25 Gbps, 8b10b) consumed by the pipeline build. **Committed.** |
| `build/` | Generated Vivado project, runs, and the output bitstream. **Git-ignored.** |

## Build

From the repo root (the `hw` wrappers locate Vivado and run `build_aclk_pipeline.tcl` in batch):

```powershell
.\hw.ps1 build          # PowerShell
```
```bash
./hw.sh build           # git bash
```

Point the wrapper at your Vivado launcher if it isn't on PATH:
`-Vivado "C:\Xilinx\Vivado\<ver>\bin\vivado.bat"` (PS) or `export VIVADO=...` (bash).

Output: `vivado/build/kria/aclk_pipeline/aclk_pipeline.runs/impl_1/uart_echo_bd_wrapper.bit`
(the internal block-design name `uart_echo_bd` is historical and kept so the
overlay/UIO identity on the board is unchanged).

Build artifacts land under `KRIA_BUILD_DIR` if set, else a repo-local
`build/kria/<name>` (see `hw.ps1`/`hw.sh`). Keep this path space-free: Vivado's
IP Integrator breaks on spaces in the project path.

## Before first hardware use

- **Install the KR260 board file** (Vivado Store → Boards → Kria KR260). The build
  applies the board preset when present and warns if it's missing.
- **Verify the PMOD/SFP package pins** in
  [`../constraints/kr260_aclk_pipeline.xdc`](../constraints/kr260_aclk_pipeline.xdc)
  against the official KR260 master XDC (the pins there are starter values).
- **Match the Vivado version to the board's Linux image** if you intend to use the
  `xmutil` app flow: the device-tree-overlay deployment is version-sensitive.

## Deployment

Getting the bitstream onto the board is handled separately (not scripted here).
Options: Vivado Hardware Manager over JTAG, `fpgautil -b <bit.bin> -o <overlay>.dtbo`
(see docs/OPERATIONS.md section 4; do not use `-f Full`, it creates no UIO device
and every AXI access bus-errors), or the Kria app flow (`xmutil unloadapp` /
`loadapp` with a `.bit.bin` + `.dtbo` + `shell.json` under
`/lib/firmware/xilinx/<app>/`).
