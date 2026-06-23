# kria-2-hardware: Fermilab TCLK / ACLK-Lite timing readout on the KR260

RTL, simulations, and Vivado bitstreams for receiving Fermilab accelerator timing
events on a **Xilinx Kria KR260** (Zynq UltraScale+, part `xck26-sfvc784-2LV-c`).
The board decodes the lab's **TCLK** and the new PIP-II **ACLK-Lite** event streams,
timestamps each event, and hands them to the PS over AXI4-Lite for software to read.

A second KR260 can act as an **ACLK-Lite signal generator** for board-to-board
testing. Everything is developed in simulation first (cocotb + Icarus, no Vivado or
hardware needed) and then built to a bitstream with Vivado.

> New here? Read **[docs/PROJECT.md](docs/PROJECT.md)** for the full architecture,
> the build-target map, and exactly what is hardware-verified vs. simulation-only.

## What it does (signal chain)

```
timing line (TCLK or ACLK-Lite, biphase-mark Manchester, on Pmod pin H12)
  -> serdec4_9MHz        recover the bit stream (80 MHz oversample)
  -> clk_byte_framer     length-aware byte framing -> event[15:0] (+ 64-bit data)
  -> aclk_readout_axi    hardware timestamp + async FIFO + AXI4-Lite register block
  -> PS (Linux, UIO)     deploy/clk_read.py drains and prints events
```

The unified decoder (`clk_rcv` = `serdec4_9MHz` + `clk_byte_framer`) reads **both**
TCLK (8-bit events) and ACLK-Lite (16-bit events + 64-bit data) from one line,
auto-detecting frame length per the ISD framing. The on-wire framing is documented
authoritatively in **[docs/aclk-lite-framing.md](docs/aclk-lite-framing.md)**.

## Status (current)

- **Unified TCLK/ACLK receiver** (`vivado/build_clk.tcl`): built, sim-validated,
  and **HW-verified** - decodes the real lab TCLK line, and decodes the generator
  board's ACLK-Lite stream board-to-board.
- **ACLK-Lite generator** (`vivado/build_aclkgen.tcl`): built and **HW-verified**
  board-to-board against the receiver.
- Earlier single-protocol builds (`build_tclk`, `build_aclk`) are kept but
  superseded by `build_clk`. See [docs/PROJECT.md](docs/PROJECT.md) for the full map
  and history.

## Prerequisites

| Tool | Notes |
|------|-------|
| Python 3.12+ | `python` on PATH; the venv is created from it |
| [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) | Icarus Verilog (`iverilog`/`vvp`), GTKWave, Verilator - for simulation |
| [AMD Vivado 2024.2](https://www.xilinx.com/support/download.html) | only for building bitstreams; the free ML Standard Edition supports the KR260 |

Make the OSS CAD Suite resolvable in **one** of two ways: add its `bin/` (and
`lib/`) to PATH, or set `OSS_CAD_SUITE` to its root (e.g. `$env:OSS_CAD_SUITE =
"C:\Users\<you>\tools\oss-cad-suite"`). Python/cocotb versions are pinned in
[requirements.txt](requirements.txt).

## Simulate (the fast inner loop)

```bash
./sim.sh setup              # create .venv + install requirements (run once)
./sim.sh run -m clk_rcv     # simulate a module (Icarus); -m <module>
./sim.sh test -m clk_rcv    # run, then open the waveform in GTKWave
./sim.sh list               # list all testbench modules
```

```powershell
.\sim.ps1 setup
.\sim.ps1 run -Module clk_rcv
.\sim.ps1 list
```

Each module under `tb/<module>/` has a cocotb `test_<module>.py` + a `runner.py`.
Tests emit a matplotlib plot under `sim_build/<module>/plots/` on completion. Key
timing testbenches: `clk_rcv`, `clk_readout`, `aclk_lite_encoder`,
`aclk_lite_gen_loopback`, `tclk_rcv`, `aclk_rcv`, `aclk_readout_axi`.

## Build a bitstream (Vivado)

```powershell
.\hw.ps1 build -Tcl vivado\build_clk.tcl -Name clk        # the unified receiver
.\hw.ps1 build -Tcl vivado\build_aclkgen.tcl -Name aclkgen # the ACLK-Lite generator
```
```bash
./hw.sh build -Tcl vivado/build_clk.tcl -Name clk
```

`hw.ps1` runs Vivado in batch (with an antivirus-flake retry loop), then packages
the bitstream with `bootgen` into `uart_echo_bd_wrapper.bit.bin` and prints its MD5.
Output lands under `build/kria/<Name>/<Name>.runs/impl_1/`. Point the wrapper at
Vivado if it is not on PATH: `-Vivado "C:\Xilinx\Vivado\2024.2\bin\vivado.bat"`.
All build targets are listed in [docs/PROJECT.md](docs/PROJECT.md).

## Deploy + run on the board

```powershell
.\hw.ps1 deploy -Name clk -DeployHost ubuntu@<board>   # scp the bin + clk_read.py + tclk_filter.py
```
Then on the board (md5-check first - all builds share the bitstream filename):
```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
sudo python3 -u clk_read.py /dev/uio4                  # add --drop 07 to filter an event
```
Per-build runbooks: [deploy/clk.md](deploy/clk.md), [deploy/tclk.md](deploy/tclk.md),
[deploy/aclk.md](deploy/aclk.md).

## Repository layout

```
rtl/
  aclk_lite/        the timing receiver + generator RTL
    clk_byte_framer.sv, clk_rcv.sv, clk_readout_top.sv   unified TCLK/ACLK decoder (current)
    aclk_lite_encoder.sv, aclk_lite_gen_timeline.sv      ACLK-Lite signal generator
    tclk_readout_top.sv, aclk_lite_readout_top.sv        earlier single-protocol tops
    aclk_lite_decoder.sv                                  clean-room Manchester decoder (legacy)
  aclk_readout/     shared readout: aclk_readout_core.sv (timestamp + async FIFO) + aclk_readout_axi.sv
  aclk_bridge/      inherited Fermilab/Evan RTL: serdec4_9MHz, TCLK_RCV, ACLK_RCV (GT), gearboxes, CRC
  *_bd_top.v        plain-Verilog block-design wrappers for each build
  async_fifo.sv, cdc_gray_count.sv, synchronizer.sv      CDC primitives
  uart_echo_top.sv, counter.sv, fifo.sv, ...             earlier bring-up + reusable modules
tb/                 cocotb testbenches, one folder per module (+ shared TX/BFM models)
constraints/        per-build .xdc pin/timing files (kr260_clk.xdc, kr260_aclkgen.xdc, ...)
vivado/             scripted RTL -> bitstream builds (build_clk.tcl, build_aclkgen.tcl, ...)
deploy/             board-side Python readers + per-build runbooks (.md)
docs/               PROJECT.md (status/architecture), aclk-lite-framing.md (authoritative framing),
                    superpowers/ (per-feature specs + plans)
resources/          Fermilab timing docs: Aclk/ (ACLK-Lite spec, PIP-II ISD), Tclk/ (TCLK docs)
sim.sh / sim.ps1    simulation wrappers (bash / PowerShell)
hw.sh  / hw.ps1     Vivado build + deploy wrappers
```

## Wrapper commands

| `sim` | Does |
|-------|------|
| `setup` | create `.venv` and install `requirements.txt` |
| `run`   | build + simulate (`-m <module>`, `-s icarus\|verilator`) |
| `wave` / `test` | open the latest waveform / run then open |
| `new <name>` | scaffold a new module + testbench |
| `list` / `clean` | list testbench modules / delete `sim_build/` |

| `hw` | Does |
|------|------|
| `build` | RTL -> bitstream -> bootgen `.bit.bin` + MD5 (`-Tcl <file> -Name <name>`) |
| `deploy` | scp the `.bit.bin` + mapped Python readers to a board (`-DeployHost`) |
| `gui` / `clean` | open the project in Vivado / delete the build dir |

bash uses `-m`/`-s`/`-Tcl`/`-Name`; PowerShell uses `-Module`/`-Sim`/`-Tcl`/`-Name`.
If PowerShell blocks the script: `powershell -ExecutionPolicy Bypass -File .\sim.ps1 ...`.

## Origin

This repo started from a SystemVerilog + cocotb simulation skeleton (the `counter`
module and the `sim`/`hw` wrappers are its legacy). It has since become the Fermilab
PIP-II timing readout above. The simulation-first workflow and the scripted Vivado
flow carried over unchanged.
