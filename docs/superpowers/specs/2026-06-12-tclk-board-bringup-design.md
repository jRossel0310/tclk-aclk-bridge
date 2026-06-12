# TCLK board bring-up: real signal -> H12 -> decode -> PS stream

Date: 2026-06-12
Status: approved design, pre-implementation

## Goal

Get a real Fermilab TCLK signal decoding on the KR260 and prove it reaches the
PS. A 3.3V baseband TCLK (from an external RF-demod + comparator front-end) enters
package pin **H12** (PMOD1 pin 1, LVCMOS33), is decoded and timestamped in the PL,
buffered across the clock-domain crossing, and read by the PS over UIO at AXI base
`0x8000_0000`. A command-line PS script streams each decoded event (code, is_tclk,
hardware timestamp, and EVENT/NULL/ERROR counts) to the terminal until Ctrl-C.

Success = run the script on the board, see real TCLK event codes scroll by with
monotonic timestamps and a climbing EVENT_COUNT.

This reuses the already-sim-validated `tclk_readout_top` (TCLK_RCV + adapter + the
decoder-agnostic AXI readout, `DROP_NULL=0`). Only the board integration (clocking,
AXI-to-PS, a debug register, the Vivado BD, the PS script) is new.

## Non-goals (explicitly out of scope for this milestone)

- Redis publishing / the PS bridge (Phase C, later).
- Automated event-rate validation against the TCLK event table.
- White Rabbit / PPS timestamp discipline (pps tied 0).
- An on-board TCLK generator / loopback (user has the real signal + front-end).

## Architecture / data flow

```
TCLK (10 MHz biphase-mark, 3.3V baseband)
  -> H12 (LVCMOS33 input, via carrier auto-direction translator)
  -> tclk_readout_top
       TCLK_RCV (serdec4_9MHz @ clk_80m + TCLK_DESERIALIZER2 @ clk_40m)
         -> adapter (aclk_valid=~DAVn, event={8'h00,DATA}, flags=is_tclk, error=PERR pulse)
         -> aclk_readout_axi (timestamp + null-drop-off packer + async FIFO + AXI4-Lite slave)
       + raw-TCLK activity counter (clk_80m) -> dbg_word (CDC to AXI domain)
  -> AXI4-Lite @ 0x8000_0000 (LPD M_AXI_HPM0_LPD)
  -> UIO (existing overlay, generic-uio)
  -> deploy/tclk_read.py (drain loop) -> terminal
```

Clock domains: `clk_80m` (80 MHz, serdec oversample), `clk_40m` (40 MHz, deserializer
+ readout timestamp), `s_axi_aclk` (100 MHz, AXI/PS-facing). The readout already
handles the 40 MHz -> 100 MHz crossing (async FIFO + Gray-coded counters).

## Components and changes

### 1. `rtl/aclk_readout/aclk_readout_axi.sv` (additive, low-risk)
Add a generic read-only debug input and register:
- New input `input logic [31:0] dbg_word` (sampled in the AXI domain; caller supplies
  it already synchronized to `s_axi_aclk`).
- New register `0x28 DEBUG` (rsel `'d10`) returning `dbg_word`.
- All existing behavior unchanged. The ACLK/Manchester instantiations and the AXI
  testbench wrapper must tie `dbg_word` to `32'd0` (SystemVerilog has no default
  port value), so update `aclk_lite_readout_top.sv` and `tb_aclk_readout_axi_top.sv`.

### 2. `rtl/aclk_lite/tclk_readout_top.sv` (additive)
Add a raw-TCLK activity path feeding `dbg_word`, so the PS can tell "signal present
but not locking" from "no signal at all" (the diagnostic we lose by skipping the
loopback):
- 2-FF synchronize `tclk` into `clk_80m`, edge-detect it, and count transitions with
  a free-running counter in `clk_80m` (80 MHz oversamples the <=20 MHz edge rate).
- Cross the count to the AXI domain with the existing `cdc_gray_count` (W=30).
- 2-FF synchronize the live `tclk` level and serdec `SIG_ERR` into `s_axi_aclk`.
- `dbg_word = { sig_err_sync, raw_level_sync, transition_count[29:0] }`
  (bit31 = sig_err, bit30 = raw level, bits[29:0] = transition count).
- Pass `dbg_word` into `aclk_readout_axi`. `pps` stays tied 0.

### 3. `rtl/tclk_readout_bd_top.v` (new, plain Verilog)
Thin BD wrapper (same role as `uart_echo_bd_top.v`): instantiates `tclk_readout_top`
and exposes ports for the block design, with `X_INTERFACE` attributes so Vivado
infers the AXI4-Lite slave and its clock/reset association:
- AXI: `s_axi_*` tagged as interface `S_AXI`; `s_axi_aclk` tagged
  `ASSOCIATED_BUSIF S_AXI`, `ASSOCIATED_RESET s_axi_aresetn`; `s_axi_aresetn` tagged
  `POLARITY ACTIVE_LOW`. `apply_bd_automation` connects the LPD master to this.
- Plain ports (connected manually in the BD): `clk_80m`, `clk_40m`, `rstn` (rx-side
  active-low reset), `tclk` (external -> H12). `pps` tied 0 inside the wrapper.
- `tclk_readout_top`'s discrete `dbg_*` outputs are left unconnected (the PS reads
  debug via the AXI DEBUG register instead).

### 4. `constraints/kr260_tclk.xdc` (new)
`set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports tclk]` (the BD
external input port for the TCLK line). Note to verify the physical connector
position against the carrier silkscreen.

### 5. `vivado/build_tclk.tcl` (new, cloned from `build_pltest.tcl`)
- `proj_name=tclk`, `design_name=uart_echo_bd` (so the bitstream is
  `uart_echo_bd_wrapper.bit.bin` and the existing overlay loads unchanged), honors
  `KRIA_BUILD_DIR`.
- PS preset; enable three PL clocks: `PL0=100` (AXI), `PL1=80`, `PL2=40`; LPD master
  GP2 on, FPD GP0/GP1 off.
- Add sources: the readout chain (`synchronizer.sv`, `async_fifo.sv`,
  `cdc_gray_count.sv`, `aclk_readout_core.sv`, `aclk_readout_axi.sv`), the TCLK
  decoder (`serdec4_9MHz.v`, `TCLK_DESERIALIZER2.v`, `TCLK_RCV.v`),
  `tclk_readout_top.sv`, and the wrapper `tclk_readout_bd_top.v`. Add the XDC.
- Instantiate `tclk_readout_bd_top` as a module-reference BD cell.
- `apply_bd_automation` (axi4 rule) on its inferred `S_AXI` interface (Master =
  `M_AXI_HPM0_LPD`, clocks Auto).
- Tie the auto proc_sys_reset `dcm_locked` high (xlconstant), as proven.
- Connect `pl_clk1 -> clk_80m`, `pl_clk2 -> clk_40m`, `peripheral_aresetn -> rstn`.
  (`s_axi_aclk`/`s_axi_aresetn` are wired by the automation.)
- Create an external **input** BD port for `tclk` and connect
  `tclk_readout_bd_top/tclk` to it (this port is constrained to H12 in the XDC).
- `assign_bd_address`, validate, `make_wrapper -import`, synth -> impl -> bitstream.

### 6. `deploy/tclk_read.py` (new, extends `pltest.py`)
Open `/dev/uioN`, mmap offset 0. Loop:
- Read `STATUS` (0x00). While not empty (bit0==0): read `EVENT` (0x04 ->
  `{flags,event}`), `DATA_HI/LO`, `TS_HI/LO`, write `POP` (0x18), print one line.
- Every ~1 s print a stats line: `EVENT_COUNT`/`NULL_COUNT`/`ERROR_COUNT` (0x1C/20/24)
  and the `DEBUG` register (0x28) decoded as edges / level / sig_err.
- Convert the 64-bit timestamp (clk_40m ticks, 25 ns) to a delta in microseconds
  between consecutive events.
- Ctrl-C clean exit. Takes the uio device as argv[1] (default `/dev/uio4`).

### 7. `deploy/tclk.md` (new)
Build (`.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk`) -> bootgen (reuse
`uart_echo.bif`) -> scp over the IPv6 link-local -> `xmutil unloadapp` +
`fpgautil -b ... -f Full` -> `sudo python3 tclk_read.py /dev/uioN`. Plus the wiring
note (front-end push-pull into H12, GND to a Pmod GND pin).

## Register map (read over UIO at 0x8000_0000)

| Off | Name | Meaning |
|----|------|---------|
| 0x00 | STATUS | bit0 empty, bit1 overflow (sticky) |
| 0x04 | EVENT | {FLAGS[15:0], EVENT[15:0]}; FLAGS bit0 has_data, bit1 is_tclk |
| 0x08/0x0C | DATA_HI/LO | 64-bit payload (0 for TCLK) |
| 0x10/0x14 | TS_HI/LO | 64-bit hardware timestamp (clk_40m ticks) |
| 0x18 | POP | write to advance the FIFO head |
| 0x1C/0x20/0x24 | EVENT/NULL/ERROR_COUNT | diagnostics |
| 0x28 | DEBUG | {sig_err[31], raw_level[30], tclk_transitions[29:0]} (new) |

## Reset strategy

`s_axi_aclk`/`s_axi_aresetn` come from the AXI automation (pl_clk0 domain). The
rx-side `rstn` is driven by the same `proc_sys_reset`'s `peripheral_aresetn`
(active-low). Our modules assert reset asynchronously, so feeding a pl_clk0-synced
reset into the clk_40m/clk_80m domains is acceptable for bring-up (async assert;
the deassert edge is the only metastability risk and is benign here). `dcm_locked`
is tied high so resets actually release (the proven KR260 gotcha).

## Testing / validation

- After the additive RTL changes, re-run the existing sims and require green:
  `tb/aclk_readout`, `tb/aclk_readout_axi`, `tb/aclk_lite_readout`, `tb/tclk_readout`.
  The `tb/tclk_readout` cocotb test gains a check that reads the new `0x28 DEBUG`
  register over the AXI BFM and asserts the transition count climbs while the line
  toggles and that the level/sig_err bits read back sanely (`dbg_word` is delivered
  already synchronized to `s_axi_aclk`, so the BFM can read it directly).
- Syntax-check `tclk_readout_bd_top.v` with iverilog (interface attributes are
  Vivado-only pragmas / ignored by iverilog).
- On-board: `tclk_read.py` shows events + climbing EVENT_COUNT = success. The DEBUG
  register localizes failures (transitions climbing but EVENT_COUNT flat = signal
  present, decoder not locking; transitions flat = no signal/front-end/pin issue).

## Risks and mitigations

- **X_INTERFACE inference on a module-reference cell** is the newest piece. If
  `apply_bd_automation` won't recognize the slave in batch, fall back to packaging
  `tclk_readout_top` as a Vivado IP (documented alternative). Validate the BD builds
  before committing to the long synth/impl.
- **80/40 MHz exactness from PS PLLs**: Vivado reports the achieved frequency; 80 and
  40 are normally exact. If not, nudge via the clock config or fall back to a
  Clocking Wizard.
- **Skipped loopback** means first-light debugging on the real signal; the DEBUG
  register is the mitigation (raw activity independent of decode).
- **Vivado IPI batch flakes** ("couldn't read file", antivirus): the `hw.ps1` retry
  loop covers it.

## Prerequisites (user/hardware)

- Front-end drives push-pull 3.3V CMOS into H12 (auto-direction translator needs a
  real driver, not open-drain), GND referenced to a Pmod GND pin.
- Signal is ~10 MHz biphase-mark (TCLK_RATE stays 1).
- KR260 board file installed; the existing UIO overlay present on the board.

## Implementation order

1. RTL: `aclk_readout_axi` DEBUG register + tie-offs in the other instantiations;
   `tclk_readout_top` activity counter + dbg_word; re-run all four sims green.
2. `tclk_readout_bd_top.v` wrapper; iverilog syntax check.
3. `constraints/kr260_tclk.xdc` + `vivado/build_tclk.tcl`.
4. `deploy/tclk_read.py` + `deploy/tclk.md`.
5. Build, deploy, first light on the board.
