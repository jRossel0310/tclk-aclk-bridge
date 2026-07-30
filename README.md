# kria-2-hardware: Fermilab TCLK to ACLK timing pipeline on the KR260

RTL, simulations, and the Vivado bitstream for the single-board Fermilab timing
pipeline on a **Xilinx Kria KR260** (Zynq UltraScale+, part `xck26-sfvc784-2LV-c`).
One board closes the whole timing-link loop: it receives real Fermilab **TCLK**,
White-Rabbit-timestamps every event with an absolute `{sec, ns}` UTC time, re-encodes
the event stream as gigabit **ACLK** out the SFP+ transceiver, loops that back in over
a fiber jumper, decodes it again on the same timebase, mirrors it as **ACLK-Lite**
(Manchester) on a Pmod pin, and publishes both readouts into Redis on the PS.

Everything is developed in simulation first (cocotb + Icarus, no Vivado or hardware
needed) and then built to one bitstream with Vivado.

> **New here?**
> - Running the board? Start with the operator runbook **[docs/OPERATIONS.md](docs/OPERATIONS.md)**.
> - Want the architecture, the module-to-file map, and what is hardware-verified vs.
>   simulation-only? Read **[docs/PROJECT.md](docs/PROJECT.md)**.
> - Taking over development? Start with the comprehensive
>   **[repository maintainer guide](docs/MAINTAINER_GUIDE.md)**.

## What it does (signal chain)

```
TCLK (H12, 3.3V biphase-mark)  +  White Rabbit 10 MHz (E10) + PPS (E12)
  -> tclk_readout_top      decode TCLK + WR-timestamp each event
  -> aclk_readout_axi      async FIFO + AXI4-Lite register block  -> PS/UIO (tclk_read.py)
  -> aclk_tclk_encoder     re-encode TCLK events into ACLK frames
  -> aclkgt_gt (GT/SFP)    broadcast gigabit ACLK out the SFP+  --fiber loop-->  same GT RX
  -> aclk_gt_readout_top   decode ACLK + WR-timestamp against the same timeline
  -> aclk_readout_axi      async FIFO + AXI4-Lite register block  -> PS/UIO (aclk_read.py)
  -> aclk_lite_bridge + aclk_lite_encoder   drive ACLK-Lite (Manchester) out on B10
```

`wr_timebase` (+ `wr_timebase_axi`) turns the White Rabbit 10 MHz + PPS into the shared
`{sec, ns}` timebase that stamps both readouts, so TCLK-in and looped-back-ACLK events
carry comparable absolute times. The on-wire ACLK-Lite framing is documented
authoritatively in **[docs/aclk-lite-framing.md](docs/aclk-lite-framing.md)**.

## Status

Hardware-validated in a 15.6 h dual-source capture (5.55 M events/source, zero loss),
2026-07-16.

## Prerequisites

| Tool | Notes |
|------|-------|
| Python 3.12+ | `python` on PATH; the venv is created from it |
| [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) | Icarus Verilog (`iverilog`/`vvp`), GTKWave, Verilator - for simulation |
| [AMD Vivado 2024.2](https://www.xilinx.com/support/download.html) | only for building the bitstream; the free ML Standard Edition supports the KR260 |

Make the OSS CAD Suite resolvable in **one** of two ways: add its `bin/` (and
`lib/`) to PATH, or set `OSS_CAD_SUITE` to its root (e.g. `$env:OSS_CAD_SUITE =
"C:\Users\<you>\tools\oss-cad-suite"`). Python/cocotb versions are pinned in
[requirements.txt](requirements.txt).

## Quickstart

```powershell
.\sim.ps1 setup                                # create .venv + install requirements (once)
.\sim.ps1 run -Module aclk_pipeline_chain      # simulate the end-to-end pipeline chain
.\hw.ps1 build                                 # RTL -> bitstream -> bootgen .bit.bin + MD5
```

`hw.ps1 build` defaults to the pipeline design (`vivado/build_aclk_pipeline.tcl`), runs
Vivado in batch, then packages the bitstream with `bootgen` into
`uart_echo_bd_wrapper.bit.bin` and prints its MD5. Output lands under
`build\kria\aclk_pipeline\aclk_pipeline.runs\impl_1\`. Point the wrapper at Vivado if it
is not on PATH: `-Vivado "C:\Xilinx\Vivado\2024.2\bin\vivado.bat"`.

To load, wire, run a capture, and get the data out, follow
**[docs/OPERATIONS.md](docs/OPERATIONS.md)**.

## Simulate (the fast inner loop)

```bash
./sim.sh setup                          # create .venv + install requirements (run once)
./sim.sh run -m aclk_pipeline_chain     # simulate a module (Icarus); -m <module>
./sim.sh test -m aclk_pipeline_chain    # run, then open the waveform in GTKWave
./sim.sh list                           # list all testbench modules
```

Each module under `tb/<module>/` has a cocotb `test_<module>.py` + a `runner.py`.
Tests emit a matplotlib plot under `sim_build/<module>/plots/` on completion. The
end-to-end pipeline chain lives in `tb/aclk_pipeline_chain`; the per-block testbenches
(`tclk_rcv`, `aclk_rcv`, `aclk_readout_axi`, `aclk_lite_encoder`, `aclk_lite_bridge`,
`wr_timebase`, ...) exercise each stage in isolation.

## Repository layout

```
rtl/
  aclk_lite/        tclk_readout_top.sv (TCLK decode + WR timestamp),
                    aclk_lite_encoder.sv (ACLK-Lite Manchester out),
                    clk_rcv.sv + clk_byte_framer.sv (testbench-support decoder only)
  aclk_gt/          aclk_gt_readout_top.sv (GT ACLK decode + timestamp),
                    aclk_tclk_encoder.v (TCLK -> ACLK frame gearbox)
  aclk_readout/     shared readout: aclk_readout_core.sv (timestamp + async FIFO) + aclk_readout_axi.sv
  aclk_bridge/      inherited Fermilab/Evan RTL: serdec, TCLK_RCV, ACLK_RCV (GT), gearboxes, CRC
  wr_timebase.sv, wr_timebase_axi.sv                     shared White Rabbit {sec, ns} timebase
  aclk_lite_bridge.v                                     ACLK event -> ACLK-Lite adapter
  aclk_pipeline_bd_top.v                                 integrated block-design top
  async_fifo.sv, cdc_gray_count.sv, synchronizer.sv, ... CDC primitives
tb/                 cocotb testbenches, one folder per module (+ shared TX/BFM models)
constraints/        kr260_aclk_pipeline.xdc pin/timing file
vivado/             build_aclk_pipeline.tcl (the one build) + ip/ (the aclkgt_gt GT IP)
deploy/             board-side Python readers/publishers + runbooks (capture.md, redis.md, wr.md)
docs/               OPERATIONS.md (runbook), PROJECT.md (architecture),
                    aclk-lite-framing.md (authoritative framing), generated/ (interface guide)
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
| `build` | RTL -> bitstream -> bootgen `.bit.bin` + MD5 (defaults to the pipeline design) |
| `deploy` | scp the `.bit.bin` + board-side Python to a board (`-DeployHost`) |
| `gui` / `clean` | open the project in Vivado / delete the build dir |

bash uses `-m`/`-s`/`-Tcl`/`-Name`; PowerShell uses `-Module`/`-Sim`/`-Tcl`/`-Name`.
If PowerShell blocks the script: `powershell -ExecutionPolicy Bypass -File .\sim.ps1 ...`.

## Origin

This repo started from a SystemVerilog + cocotb simulation skeleton (the `sim`/`hw`
wrappers are its legacy). The Vivado block design still carries the historical internal
name `uart_echo_bd`, which is why the bitstream file is `uart_echo_bd_wrapper.bit.bin`;
the name is cosmetic and keeps the board overlay/UIO identity stable across builds. The
simulation-first workflow and the scripted Vivado flow carried over unchanged.
