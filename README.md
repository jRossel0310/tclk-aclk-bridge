# sv-sim-skeleton — SystemVerilog + cocotb simulation starter

A reusable starting point for SystemVerilog projects: write RTL, test it in
Python with [cocotb](https://www.cocotb.org/), and view waveforms — all locally,
no Vivado or hardware required. Clone it, run `setup`, and you have a passing
smoke test in under a minute.

> Simulation is the fast inner loop and needs no Vivado or hardware. A scripted
> **Vivado bitstream flow** for the **Xilinx Kria KR260 (Zynq UltraScale+)** also
> lives here — see [Hardware: KR260 bitstream](#hardware-kr260-bitstream) below.

## Prerequisites

Install these once, per machine:

| Tool | Notes |
|------|-------|
| Python 3.12+ | `python` on PATH; the venv is created from it |
| [OSS CAD Suite](https://github.com/YosysHQ/oss-cad-suite-build) | provides Icarus Verilog (`iverilog`/`vvp`), GTKWave, and Verilator |

Make the OSS CAD Suite tools resolvable in **one** of two ways:

- add its `bin/` (and `lib/`) to your PATH, **or**
- set `OSS_CAD_SUITE` to its root, e.g.
  `export OSS_CAD_SUITE=/c/Users/<you>/tools/oss-cad-suite` (bash) /
  `$env:OSS_CAD_SUITE = "C:\Users\<you>\tools\oss-cad-suite"` (PowerShell).

The `sim` wrappers auto-detect either and put `bin`+`lib` on PATH for the sim
process. Python/cocotb versions are pinned in [requirements.txt](requirements.txt).

## Quick start

```bash
# git bash / MSYS
./sim.sh setup        # create .venv + install requirements (run once)
./sim.sh run          # build + simulate the example module (Icarus)
./sim.sh test         # run, then open the waveform in GTKWave
```

```powershell
# PowerShell
.\sim.ps1 setup
.\sim.ps1 run
.\sim.ps1 test
```

Expect `TESTS=1 PASS=1 FAIL=0` and an FST at `sim_build/counter/counter.fst`.

## Layout

```
.
  rtl/                 # synthesizable RTL, one file per module
    counter.sv         # example module (proves the toolchain)
    uart_echo_top.sv   # KR260 bring-up top: RX -> FIFO -> TX echo
  tb/                  # testbenches, one folder per module
    counter/
      test_counter.py  # cocotb tests
      runner.py        # cocotb Python runner (SIM switch lives here)
  constraints/         # hardware pin/timing constraints (.xdc) — not used by sim
    kr260.xdc          # KR260 PMOD pin map for uart_echo_top
  vivado/              # scripted Vivado RTL -> bitstream flow (KR260)
    build.tcl          # block design + synth/impl/bitstream
  sim_build/           # generated build + waveforms (git-ignored)
  .venv/               # Python venv (git-ignored; created by `setup`)
  requirements.txt     # pinned Python deps
  sim.sh / sim.ps1     # simulation task wrappers (bash / PowerShell)
  hw.sh  / hw.ps1      # hardware build wrappers (bash / PowerShell)
```

## Add a new module

```bash
./sim.sh new fifo          # scaffolds rtl/fifo.sv + tb/fifo/{runner.py,test_fifo.py}
./sim.sh run -m fifo       # the stub passes immediately; edit from there
```

(`\.sim.ps1 new fifo` in PowerShell.) The scaffold is a minimal reset-to-zero
module + cocotb smoke test, so a freshly scaffolded module passes out of the box
— replace the body of each with your real logic and checks.

## Wrapper commands

| Command | Does |
|---------|------|
| `setup` | create `.venv` and install `requirements.txt` |
| `run`   | build + simulate (`-m <module>`, `-s icarus\|verilator`) |
| `wave`  | open the latest waveform in GTKWave |
| `test`  | `run`, then `wave` |
| `new <name>` | scaffold a new module + testbench |
| `clean` | delete `sim_build/` |
| `list`  | list testbench modules |

bash uses `-m`/`-s`; PowerShell uses `-Module`/`-Sim`. If PowerShell blocks the
script, run once `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned`, or invoke
as `powershell -ExecutionPolicy Bypass -File .\sim.ps1 run`.

In **VS Code**: `Ctrl+Shift+B` runs the sim; `Terminal > Run Task...` lists
setup / run / run+waveform / open waveform / clean.

### Manual equivalent (what the wrapper does)

```bash
# git bash — note: source Scripts/activate (not bin/) on Windows
source .venv/Scripts/activate
cd tb/counter
python runner.py
gtkwave ../../sim_build/counter/counter.fst
```

## Switch simulators (one line)

In a module's `runner.py`, change `SIM = os.getenv("SIM", "icarus")`, or set the
env var without editing code — the wrappers expose `-s`/`-Sim`:

```bash
SIM=verilator python runner.py      # or: ./sim.sh run -s verilator
```

> **Verilator on Windows note:** Verilator is bundled with OSS CAD Suite, but
> running it natively on Windows (and cocotb+Verilator specifically) is finicky
> — it shells out to a C++ compiler and a Perl wrapper. If Icarus chokes on real
> SystemVerilog and you need Verilator, the smoothest path is WSL2 + Ubuntu
> (`sudo apt install verilator`). The `SIM` switch is wired up regardless.

## Hardware: KR260 bitstream

The same RTL also builds to a bitstream for the **Kria KR260** (Zynq UltraScale+,
part `xck26-sfvc784-2LV-c`). UltraScale+ has no open-source bitstream path, so
this stage needs **AMD Vivado** — the free **ML Standard Edition** supports the
KR260. The first hardware target is [`rtl/uart_echo_top.sv`](rtl/uart_echo_top.sv):
a UART loopback (`uart_receiver` → `fifo` → `uart_transmitter`) — type into a
serial terminal and the characters echo back.

The clock and reset come from the Zynq PS inside a Vivado block design
(`pl_clk0` at 100 MHz, an active-high reset from `pl_resetn`) — Kria has no
free-running user oscillator wired to general PL logic, so this is the standard
flow. The build is fully scripted in [`vivado/build.tcl`](vivado/build.tcl).

```powershell
.\hw.ps1 build      # RTL -> bitstream (PowerShell)
```
```bash
./hw.sh build       # RTL -> bitstream (git bash)
```

Point the wrapper at Vivado if it isn't on PATH: `-Vivado "C:\Xilinx\Vivado\<ver>\bin\vivado.bat"`
(PS) or `export VIVADO=...` (bash). Output bitstream lands under
`vivado/build/uart_echo.runs/impl_1/`.

**Before the first hardware run:** install the KR260 board file (Vivado Store →
Boards → Kria KR260), and **verify the PMOD pins** in
[`constraints/kr260.xdc`](constraints/kr260.xdc) against the official KR260 master
XDC. See [`vivado/README.md`](vivado/README.md) for details. Simulate the echo
first — `./sim.sh run -m uart_echo_top` — before spending Vivado time.

> Loading the bitstream onto the board (JTAG Hardware Manager, `fpgautil`, or the
> Kria `xmutil loadapp` app flow) is done on the board itself and is not scripted
> here.

## GTKWave fails to open ("libpixbufloader-svg.dll" / Gtk bail out)

GTKWave is a native GTK app and needs the OSS CAD Suite's GTK runtime
environment (normally set by the suite's `environment.bat`). Launched bare, it
crashes with `Unable to load image-loading module ...libpixbufloader-svg.dll`
because the shipped `loaders.cache` lists an svg loader that isn't on disk.

Both wrappers handle this automatically before launching GTKWave: they export
`GTK_EXE_PREFIX`/`GTK_DATA_PREFIX`/`GDK_PIXBUF_MODULE_FILE` and rebuild the loader
cache with `gdk-pixbuf-query-loaders.exe --update-cache`. The cache rebuild is
permanent. If you reinstall OSS CAD Suite, just run `wave` once to repair it.
