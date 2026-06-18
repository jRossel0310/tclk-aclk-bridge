# ACLK-Lite readout board build Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a board-buildable ACLK-Lite readout (Manchester ADM -> shared timestamp/FIFO/AXI readout -> PS), generated and switched the same way as the existing TCLK build, without touching the TCLK path.

**Architecture:** Reuse the already-sim-validated chain `aclk_lite_decoder` -> `aclk_lite_readout_top` -> shared `aclk_readout_axi`. Add the missing board glue: a plain-Verilog BD wrapper, a `build_aclk.tcl` mirroring `build_tclk.tcl` (one MMCM oversample clock instead of 80/40 MHz), an XDC putting the Manchester line on H12, and a PS reader. Bring `aclk_lite_readout_top` to parity with the TCLK top (wire `mmcm_locked`, `dbg_hb`, and a line-activity DEBUG word).

**Tech Stack:** SystemVerilog / Verilog (Vivado 2024.2, part `xck26-sfvc784-2LV-c`), cocotb 2.0 + Icarus for sim, Python 3 for the PS reader, PowerShell (`hw.ps1`) for the build/deploy wrapper.

## Global Constraints

- Project style: NEVER use em dashes anywhere (code comments, docs, commit messages). Use " - " or rephrase.
- Do NOT edit any TCLK-path file: `vivado/build_tclk.tcl`, `constraints/kr260_tclk.xdc`, `rtl/aclk_lite/tclk_readout_top.sv`, `rtl/tclk_readout_bd_top.v`, `deploy/tclk_read.py`. A TCLK build must remain byte-for-byte equivalent.
- Do NOT change the shared readout RTL behavior: `rtl/aclk_readout/aclk_readout_axi.sv`, `aclk_readout_core.sv`, `rtl/async_fifo.sv`, `rtl/cdc_gray_count.sv`, `rtl/synchronizer.sv`. They are already parameterized for both paths.
- AXI register map is the shared 16-byte-spaced map: STATUS 0x00, EVENT 0x10, DATA_HI 0x20, DATA_LO 0x30, TS_HI 0x40, TS_LO 0x50, POP 0x60, EVENT_COUNT 0x70, NULL_COUNT 0x80, ERROR_COUNT 0x90, DEBUG 0xA0, HEARTBEAT 0xB0, LOCK 0xC0, FILTER_CFG 0xD0 (W), FILTERED_COUNT 0xE0 (R).
- ACLK uses `DROP_NULL = 1` (the readout default), opposite of the TCLK top.
- Board oversample clock = 120 MHz (within the spec's 100-125 MHz), `OVERSAMPLE = 12` on the board (120 MHz / ~10 MHz line). Both are documented as tunable at bring-up against a live source; they do not affect simulation.
- Sim is run with: `.\sim.ps1 run -Module aclk_lite_readout` (Icarus). Quick RTL elaboration uses `iverilog -g2012`.
- Bitstream `design_name = uart_echo_bd` so the wrapper is `uart_echo_bd_wrapper.bit.bin` and the existing overlay loads unchanged. `proj_name = aclk`.

---

### Task 1: Bring `aclk_lite_readout_top` to parity (mmcm_locked + dbg_hb + DEBUG activity word)

**Files:**
- Modify: `rtl/aclk_lite/aclk_lite_readout_top.sv`
- Test: `tb/aclk_lite_readout/test_aclk_lite_readout.py`

**Interfaces:**
- Consumes: `aclk_readout_axi` (already has inputs `mmcm_locked`, `dbg_word[31:0]` and output `dbg_hb`), `cdc_gray_count #(.W())`.
- Produces: `aclk_lite_readout_top` gains input `mmcm_locked` and output `dbg_hb`; the readout's DEBUG register (0xA0) now carries `{1'b0, line_level, line_edge_count[29:0]}` and LOCK (0xC0) reflects `mmcm_locked`. The board wrapper (Task 2) relies on these two new ports.

Today `aclk_lite_readout_top` instantiates `aclk_readout_axi` with `.dbg_word(32'd0)` and omits `mmcm_locked` (left floating) and `dbg_hb`. This task wires them.

- [ ] **Step 1: Write the failing test additions**

In `tb/aclk_lite_readout/test_aclk_lite_readout.py`, add the two register offsets next to the existing ones (line ~32-34 defines `STATUS ... ERROR_COUNT`). Add after that block:

```python
DEBUG, LOCK = 0xA0, 0xC0
```

In `reset_dut`, drive the new input high (the MMCM is "locked" in sim). Add this line alongside the other initial assignments (e.g. right after `dut.line.value = 1`):

```python
    dut.mmcm_locked.value = 1
```

At the end of `test_manchester_to_axi`, after the existing `EVENT_COUNT` / `ERROR_COUNT` assertions (around line 194-195), add:

```python
    lock = await axi_read(dut, LOCK)
    assert lock & 0x1 == 1, f"LOCK 0x{lock:08X} did not reflect mmcm_locked=1"
    dbg = await axi_read(dut, DEBUG)
    edges = dbg & 0x3FFFFFFF
    assert edges > 0, f"DEBUG edge count {edges} did not climb while the line toggled"
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\sim.ps1 run -Module aclk_lite_readout`
Expected: FAIL during elaboration/run because `aclk_lite_readout_top` has no `mmcm_locked` port (cocotb: `dut.mmcm_locked` AttributeError) and/or the LOCK/DEBUG assertions fail (LOCK reads 0 from a floating input, DEBUG reads 0).

- [ ] **Step 3: Add the new ports and DEBUG/LOCK wiring**

In `rtl/aclk_lite/aclk_lite_readout_top.sv`, add the `mmcm_locked` input and `dbg_hb` output. In the port list, add `mmcm_locked` near the AXI clock inputs and `dbg_hb` in the debug section:

```systemverilog
    // ---- recovered-RX / oversampling domain ----
    input  logic        rx_clk,
    input  logic        rx_rstn,
    input  logic        pps,
    input  logic        line,              // Manchester serial input
    input  logic        mmcm_locked,       // MMCM locked (async) -> AXI 0xC0 LOCK
```

and in the debug output group:

```systemverilog
    // ---- debug (rx domain) ----
    output logic        dbg_event_valid,
    output logic        dbg_data_valid,
    output logic        dbg_is_tclk,
    output logic        dbg_hb,            // deep cdc heartbeat[12] -> pin probe
    output logic        dropped_null
```

After the adapter wires (after `assign dbg_is_tclk = is_tclk;`), add the line-activity diagnostic:

```systemverilog
    // ---- line-activity diagnostic (-> DEBUG register 0xA0) ----
    // 2FF-synchronize the async Manchester line into rx_clk, count every transition,
    // and cross the count to the AXI domain with the same Gray counter the readout
    // uses elsewhere. A live line makes this climb even if framing never decodes, so
    // the PS can tell "signal present but not decoding" from "no signal at the pin".
    logic line_m, line_s2, line_s_d;
    always_ff @(posedge rx_clk or negedge rx_rstn) begin
        if (!rx_rstn) begin
            line_m   <= 1'b1;
            line_s2  <= 1'b1;
            line_s_d <= 1'b1;
        end else begin
            line_m   <= line;
            line_s2  <= line_m;
            line_s_d <= line_s2;
        end
    end
    wire line_edge = line_s2 ^ line_s_d;        // one rx_clk pulse per transition

    wire [29:0] edge_count;
    cdc_gray_count #(.W(30)) u_cnt_edge (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(line_edge),
        .dst_clk(s_axi_aclk), .count_dst(edge_count));

    // Live level, synchronized into the AXI domain (read at 0xA0 bit30).
    logic lvl_m, lvl_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            lvl_m <= 1'b0; lvl_s <= 1'b0;
        end else begin
            lvl_m <= line; lvl_s <= lvl_m;
        end
    end

    wire [31:0] aclk_dbg_word = {1'b0, lvl_s, edge_count};
```

Then in the `aclk_readout_axi` instantiation, change `.dbg_word(32'd0),` to wire the new word and add the two ports. The instantiation block (currently ending `.dbg_word(32'd0),` then the s_axi ports) becomes:

```systemverilog
        .dbg_word      (aclk_dbg_word),
        .mmcm_locked   (mmcm_locked),
        .dbg_hb        (dbg_hb),
```

(Insert these three lines where `.dbg_word(32'd0),` was, keeping the existing s_axi port connections that follow.)

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\sim.ps1 run -Module aclk_lite_readout`
Expected: PASS. The log prints "full chain OK ..."; LOCK reads 1 and DEBUG edge count is non-zero.

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/aclk_lite_readout_top.sv tb/aclk_lite_readout/test_aclk_lite_readout.py
git commit -m "feat(aclk): parity with TCLK top - mmcm_locked, dbg_hb, line-activity DEBUG word"
```

---

### Task 2: Plain-Verilog block-design wrapper `aclk_readout_bd_top.v`

**Files:**
- Create: `rtl/aclk_readout_bd_top.v`

**Interfaces:**
- Consumes: `aclk_lite_readout_top` (from Task 1: ports `rx_clk, rx_rstn, pps, line, mmcm_locked`, the AXI4-Lite slave, and `dbg_hb`), `cdc_gray_count`.
- Produces: module `aclk_readout_bd_top` with ports `clk_os, rstn, aclk, mmcm_locked, clkos_dbg, clk100_dbg, cdc_dbg, dbg_hb`, and the inferred AXI4-Lite slave `S_AXI` on `s_axi_aclk`/`s_axi_aresetn`. `build_aclk.tcl` (Task 3) instantiates this as a module reference `u_aclk`.

- [ ] **Step 1: Create the wrapper**

Create `rtl/aclk_readout_bd_top.v` with exactly this content (mirrors `rtl/tclk_readout_bd_top.v`, single oversample clock, external `aclk` line):

```verilog
// rtl/aclk_readout_bd_top.v
//
// Plain-Verilog block-design wrapper around aclk_lite_readout_top (SystemVerilog).
// The X_INTERFACE attributes let Vivado infer the AXI4-Lite slave (S_AXI) and its
// clock/reset association so the PS LPD master can be wired to it. pps is tied 0
// (no White Rabbit yet); the discrete dbg_* outputs go to Pmod pins for scope
// bring-up (the readout itself is read via AXI). Counterpart to tclk_readout_bd_top.v;
// the ACLK path uses ONE oversample clock (clk_os) instead of the TCLK 80/40 pair.

`timescale 1ns / 1ps

module aclk_readout_bd_top (
    // receive domain: single Manchester oversample clock (from the BD clk_wiz)
    input  wire        clk_os,
    input  wire        rstn,
    input  wire        aclk,          // raw Manchester ACLK-Lite line (LVCMOS33 baseband)
    input  wire        mmcm_locked,   // MMCM locked (async) -> AXI 0xC0 LOCK
    output wire        clkos_dbg,     // clk_os / 1024  -> Pmod pin (scope: is clk_os alive?)
    output wire        clk100_dbg,    // s_axi_aclk(pl_clk0) / 1024 -> Pmod pin (alive control)
    output wire        cdc_dbg,       // a fresh cdc_gray_count's output bit -> Pmod pin
    output wire        dbg_hb,        // deep readout heartbeat[12] -> Pmod pin

    // AXI4-Lite slave (PS clock); interfaces inferred from the attributes below
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        s_axi_aresetn,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    input  wire [7:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  wire [7:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready
);

    // OVERSAMPLE = clk_os / line-bit-rate = 120 MHz / ~10 MHz = 12 (tune at bring-up).
    aclk_lite_readout_top #(.OVERSAMPLE(12), .ADDR_WIDTH(6), .AXI_ADDR_W(8)) u_aclk (
        .rx_clk        (clk_os),
        .rx_rstn       (rstn),
        .pps           (1'b0),
        .line          (aclk),
        .mmcm_locked   (mmcm_locked),

        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),
        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready),

        .dbg_event_valid (),
        .dbg_data_valid  (),
        .dbg_is_tclk     (),
        .dbg_hb          (dbg_hb),
        .dropped_null    ()
    );

    // ---- clock-alive scope diagnostics (same idea as tclk_readout_bd_top) ----
    // Pmod level translators can't pass 120/100 MHz, so divide each clock to ~tens of
    // kHz and drive a pin. clk100_dbg (from the always-on PS clock) is the control.
    reg [9:0] div_os  = 10'd0;
    reg [9:0] div100  = 10'd0;
    always @(posedge clk_os)     div_os <= div_os + 1'b1;
    always @(posedge s_axi_aclk) div100 <= div100 + 1'b1;
    assign clkos_dbg  = div_os[9];    // ~117 kHz if clk_os alive
    assign clk100_dbg = div100[9];    // ~98 kHz (control, from pl_clk0)

    // ---- cdc_gray_count isolation probe ----
    wire [31:0] cdc_test;
    cdc_gray_count #(.W(32)) u_cdc_test (
        .src_clk(clk_os), .src_rstn(1'b1), .incr(1'b1),
        .dst_clk(s_axi_aclk), .count_dst(cdc_test)
    );
    assign cdc_dbg = cdc_test[12];    // ~3.6 kHz if cdc_gray_count works on silicon

endmodule
```

- [ ] **Step 2: Elaborate with Icarus to catch port/syntax errors**

Run (from the repo root; Icarus ships in the OSS cad suite the sim uses):

```bash
iverilog -g2012 -s aclk_readout_bd_top -o /dev/null \
  rtl/synchronizer.sv rtl/async_fifo.sv rtl/cdc_gray_count.sv \
  rtl/aclk_readout/aclk_readout_core.sv rtl/aclk_readout/aclk_readout_axi.sv \
  rtl/aclk_lite/aclk_lite_decoder.sv rtl/aclk_lite/aclk_lite_readout_top.sv \
  rtl/aclk_readout_bd_top.v
```

Expected: no output, exit 0 (elaboration clean; attributes are ignored by Icarus). If `iverilog` is not on PATH, prepend the OSS cad suite bin: `$env:OSS_CAD_SUITE\bin`.

- [ ] **Step 3: Commit**

```bash
git add rtl/aclk_readout_bd_top.v
git commit -m "feat(aclk): plain-Verilog BD wrapper aclk_readout_bd_top (mirrors tclk_readout_bd_top)"
```

---

### Task 3: XDC + `build_aclk.tcl` (the ACLK board build)

**Files:**
- Create: `constraints/kr260_aclk.xdc`
- Create: `vivado/build_aclk.tcl`

**Interfaces:**
- Consumes: module reference `aclk_readout_bd_top` (Task 2) with ports `clk_os, rstn, aclk, mmcm_locked, clkos_dbg, clk100_dbg, cdc_dbg, dbg_hb` + `S_AXI`.
- Produces: a bitstream `uart_echo_bd_wrapper.bit.bin` built into `build/kria/aclk` (via `hw.ps1`).

- [ ] **Step 1: Create the XDC**

Create `constraints/kr260_aclk.xdc` (mirrors `kr260_tclk.xdc`; `aclk` on H12, same dbg pins, async groups):

```tcl
## constraints/kr260_aclk.xdc - ACLK-Lite Manchester input
##
## ACLK-Lite rides the same TTL baseband interface as TCLK, so the Manchester line
## enters on KR260 PMOD1 pin 1 = package H12, LVCMOS33 (the same physical pin the
## TCLK build uses). Verify the connector position against the carrier-card silkscreen.

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports aclk]

## Clock-alive scope diagnostics (temporary): divided clk_os + pl_clk0 to PMOD1 pins.
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports clkos_dbg]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports clk100_dbg]
set_property -dict {PACKAGE_PIN D10 IOSTANDARD LVCMOS33} [get_ports cdc_dbg]
set_property -dict {PACKAGE_PIN D11 IOSTANDARD LVCMOS33} [get_ports dbg_hb]

## Asynchronous clock groups.
##
## The clk_wiz MMCM makes the single ~120 MHz oversample clock (clk_out1) from
## pl_clk0; it shares pl_clk0 as a physical source but is NOT phase-comparable to the
## ~100 MHz PS/AXI clock (clk_pl_*). Every PS<->rx crossing goes through the readout's
## async FIFO (gray pointers + 2-FF syncs), safe by construction. Without this the
## tool times those CDC paths and reports large false negative slack. Declaring the
## domains asynchronous excludes those crossings.
set_clock_groups -name async_ps_vs_rx -asynchronous \
    -group [get_clocks -filter {NAME =~ "clk_pl_*"}] \
    -group [get_clocks -filter {NAME =~ "clk_out*clk_wiz*"}]
```

- [ ] **Step 2: Create the build TCL**

Create `vivado/build_aclk.tcl` (mirrors `build_tclk.tcl`; single MMCM output, `aclk` pin, ACLK source list). Full content:

```tcl
# vivado/build_aclk.tcl - ACLK-Lite (Manchester ADM) readout on the KR260.
#
# PS (pl_clk0 100 MHz AXI + a PL MMCM 120 MHz oversample clock) + the ACLK-Lite
# readout (aclk_readout_bd_top) on the LPD AXI master at 0x8000_0000. The Manchester
# line enters on H12 (shared with the TCLK build). Reuses design_name=uart_echo_bd so
# the bitstream is named uart_echo_bd_wrapper.bit.bin and the existing overlay loads
# unchanged. Counterpart to build_tclk.tcl; build_tclk.tcl is left untouched.
#
# Build:  vivado -mode batch -source vivado/build_aclk.tcl
#    or:  .\hw.ps1 build -Tcl vivado\build_aclk.tcl -Name aclk   (PS, with AV retry)

set proj_name   aclk
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_aclk.xdc]

if {[info exists ::env(KRIA_BUILD_DIR)] && [string length $::env(KRIA_BUILD_DIR)] > 0} {
    set build_dir $::env(KRIA_BUILD_DIR)
} elseif {[info exists ::env(USERPROFILE)]} {
    set build_dir [file join $::env(USERPROFILE) kria-builds $proj_name]
} elseif {[info exists ::env(HOME)]} {
    set build_dir [file join $::env(HOME) kria-builds $proj_name]
} else {
    set build_dir [file join $script_dir build_$proj_name]
}
puts "INFO: building in $build_dir"

create_project -force $proj_name $build_dir -part $part
set bp [get_board_parts -quiet -filter {NAME =~ "*kr260*"}]
if {[llength $bp] > 0} { set_property board_part [lindex $bp 0] [current_project] }

# RTL sources: shared readout chain + ACLK-Lite Manchester decoder + readout top + BD top.
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
    [file join $rtl_dir aclk_lite aclk_lite_decoder.sv] \
    [file join $rtl_dir aclk_lite aclk_lite_readout_top.sv] \
    [file join $rtl_dir aclk_readout_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

# Zynq US+ PS with board preset; pl_clk0 (100 MHz) + the LPD AXI master.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
] $ps

# Our ACLK readout as a module reference (the Verilog BD wrapper).
set aclk [create_bd_cell -type module -reference aclk_readout_bd_top u_aclk]

# PS -> our AXI slave over the LPD master, via an AXI SmartConnect (NOT the
# apply_bd_automation interconnect+protocol_converter, which on hardware dropped read
# data for non-16-byte-aligned offsets). PS LPD master, SmartConnect, and our slave are
# all on pl_clk0 (one clock domain), so a single-clock SmartConnect is all we need.
set rst [create_bd_cell -type ip -vlnv xilinx.com:ip:proc_sys_reset:* rst_pl0]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]    [get_bd_pins rst_pl0/slowest_sync_clk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins rst_pl0/ext_reset_in]

set sc [create_bd_cell -type ip -vlnv xilinx.com:ip:smartconnect:* axi_sc]
set_property -dict [list CONFIG.NUM_SI {1} CONFIG.NUM_MI {1}] $sc
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins axi_sc/aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins axi_sc/aresetn]

connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins zynq_ultra_ps_e_0/maxihpm0_lpd_aclk]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]  [get_bd_pins u_aclk/s_axi_aclk]
connect_bd_net [get_bd_pins rst_pl0/peripheral_aresetn] [get_bd_pins u_aclk/s_axi_aresetn]

connect_bd_intf_net [get_bd_intf_pins zynq_ultra_ps_e_0/M_AXI_HPM0_LPD] [get_bd_intf_pins axi_sc/S00_AXI]
connect_bd_intf_net [get_bd_intf_pins axi_sc/M00_AXI] [get_bd_intf_pins u_aclk/S_AXI]

# Clocking Wizard: make ONE 120 MHz Manchester oversample clock from pl_clk0 INSIDE the
# PL, so we depend only on pl_clk0 (the one PL clock a runtime bitstream load leaves at
# the expected frequency). pl_clk0 is an internal net, so the MMCM input takes it with
# no buffer. The MMCM 'locked' drives the LOCK diagnostic.
set clkw [create_bd_cell -type ip -vlnv xilinx.com:ip:clk_wiz:* clk_wiz_0]
# pl_clk0's realized rate is not exactly 100 MHz; clk_wiz makes clk_in1's FREQ_HZ
# read-only and derives it from PRIM_IN_FREQ, so feed pl_clk0's exact rate in as
# PRIM_IN_FREQ or validate_bd_design trips BD 41-238.
set pl0_hz  [get_property CONFIG.FREQ_HZ [get_bd_pins zynq_ultra_ps_e_0/pl_clk0]]
set pl0_mhz [format %.6f [expr {$pl0_hz / 1000000.0}]]
set_property -dict [list \
    CONFIG.PRIM_SOURCE {No_buffer} \
    CONFIG.PRIM_IN_FREQ $pl0_mhz \
    CONFIG.CLKOUT1_REQUESTED_OUT_FREQ {120.000} \
    CONFIG.USE_LOCKED {true} \
    CONFIG.USE_RESET {true} \
    CONFIG.RESET_TYPE {ACTIVE_LOW} \
] $clkw
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk0] [get_bd_pins clk_wiz_0/clk_in1]
# Give the MMCM a real reset from pl_resetn0 so it re-locks after a runtime fpgautil
# load (the PL is reconfigured while pl_clk0 is already toggling; without a reset a
# missed lock can never re-acquire -> clk_os stays dead).
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_resetn0] [get_bd_pins clk_wiz_0/resetn]
connect_bd_net [get_bd_pins clk_wiz_0/clk_out1] [get_bd_pins u_aclk/clk_os]
# Expose MMCM locked to the PS at AXI 0xC0 (s_axi_aclk domain, always alive).
connect_bd_net [get_bd_pins clk_wiz_0/locked] [get_bd_pins u_aclk/mmcm_locked]

# Reset: tie the auto proc_sys_reset's dcm_locked HIGH (proven on the tclk/uart_echo
# builds). Gating s_axi_aresetn on clk_wiz/locked WEDGED the LPD bus on hardware (reset
# stayed asserted, the AXI slave held ARREADY=1 / RVALID=0, every read hung). Tying it
# high releases peripheral_aresetn as soon as pl_resetn deasserts, independent of the
# MMCM; the slave (clocked by the always-on pl_clk0) responds. The rx logic comes out of
# reset too and the MMCM locks within microseconds.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]
connect_bd_net [get_bd_pins $rst/peripheral_aresetn] [get_bd_pins u_aclk/rstn]

# External ACLK-Lite Manchester input -> H12 (constrained in the XDC).
create_bd_port -dir I aclk
connect_bd_net [get_bd_port aclk] [get_bd_pins u_aclk/aclk]

# Clock-alive scope diagnostics out to Pmod pins.
create_bd_port -dir O clkos_dbg
create_bd_port -dir O clk100_dbg
create_bd_port -dir O cdc_dbg
create_bd_port -dir O dbg_hb
connect_bd_net [get_bd_port clkos_dbg]  [get_bd_pins u_aclk/clkos_dbg]
connect_bd_net [get_bd_port clk100_dbg] [get_bd_pins u_aclk/clk100_dbg]
connect_bd_net [get_bd_port cdc_dbg]    [get_bd_pins u_aclk/cdc_dbg]
connect_bd_net [get_bd_port dbg_hb]     [get_bd_pins u_aclk/dbg_hb]

assign_bd_address
regenerate_bd_layout
validate_bd_design
save_bd_design

set bd_file [get_files ${design_name}.bd]
make_wrapper -files $bd_file -top -import
set_property top ${design_name}_wrapper [current_fileset]
update_compile_order -fileset sources_1

launch_runs synth_1 -jobs 4
wait_on_run synth_1
launch_runs impl_1 -to_step write_bitstream -jobs 4
wait_on_run impl_1

set bit [file join $build_dir ${proj_name}.runs impl_1 ${design_name}_wrapper.bit]
if {[file exists $bit]} {
    puts "=========================================================="
    puts "BITSTREAM: $bit"
    puts "=========================================================="
} else {
    puts "ERROR: bitstream not found - check the impl_1 run log."
    exit 1
}
```

- [ ] **Step 3: Structural review against the TCLK build (the immediate gate)**

Run a diff to confirm the only intended differences vs the proven build are the source list, the single MMCM output, the `aclk` port, and proj/xdc names:

```bash
git --no-pager diff --no-index vivado/build_tclk.tcl vivado/build_aclk.tcl
git --no-pager diff --no-index constraints/kr260_tclk.xdc constraints/kr260_aclk.xdc
```

Expected: differences confined to the items above (proj_name, xdc path, source files, the clk_wiz block dropping CLKOUT2, `clk_80m/clk_40m` -> `clk_os`, `tclk` -> `aclk`, dbg port name `clk40_dbg` -> `clkos_dbg`). No unexpected changes to the reset/SmartConnect/address topology.

- [ ] **Step 4: (Heavy, requires Vivado) Run the build**

Run: `.\hw.ps1 build -Tcl vivado\build_aclk.tcl -Name aclk`
Expected: completes synth + impl + `write_bitstream`; `hw.ps1` then bootgens and prints `BIT:`, `BIN:`, `MD5:`. The bin is `build/kria/aclk/aclk.runs/impl_1/uart_echo_bd_wrapper.bit.bin`. (This takes tens of minutes and needs Vivado 2024.2; the AV-retry loop in `hw.ps1` handles the IPI flake. If Vivado is unavailable in this environment, defer to the user and treat Step 3 as the gate.)

- [ ] **Step 5: Commit**

```bash
git add constraints/kr260_aclk.xdc vivado/build_aclk.tcl
git commit -m "feat(aclk): build_aclk.tcl + kr260_aclk.xdc (ACLK-Lite board build, H12 input)"
```

---

### Task 4: PS reader, runbook, and deploy wiring

**Files:**
- Create: `deploy/aclk_read.py`
- Create: `deploy/aclk.md`
- Modify: `hw.ps1` (the deploy `$pyMap`)

**Interfaces:**
- Consumes: the shared 16-byte AXI register map; `deploy/tclk_filter.py` (`parse_drop_codes`, `filter_cfg_word`) reused as-is.
- Produces: `aclk_read.py` (UIO reader interpreting 16-bit events + 64-bit data) and a `deploy -Name aclk` mapping that scp's `aclk_read.py` + `tclk_filter.py`.

- [ ] **Step 1: Create the reader**

Create `deploy/aclk_read.py` (mirrors `tclk_read.py`; 16-bit event id, 64-bit data column, 120 MHz tick, line-edge DEBUG word, no serdec sig_err):

```python
#!/usr/bin/env python3
"""Stream decoded ACLK-Lite events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(16-bit event id + flags + 64-bit data + 64-bit hardware timestamp), pops it, prints a
line. Every ~1 s prints a stats line: EVENT/NULL/ERROR/FILTERED counts + the DEBUG
activity register (raw Manchester line transitions, which climb even if the decoder
never frames).

    sudo python3 aclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: line_edges climbing + EVT flat => signal present but
not decoding (check OVERSAMPLE / line bit rate); line_edges flat => no signal / pin.

Output is LINE-BUFFERED on purpose so a freeze can never hide already-printed output; a
startup probe + watchdog name the exact register if an AXI read wedges the bus.
"""
import mmap, os, struct, sys, threading, time
from tclk_filter import parse_drop_codes, filter_cfg_word

try:
    sys.stdout.reconfigure(line_buffering=True)
except Exception:
    pass

def say(msg):
    print(msg, flush=True)

_args = sys.argv[1:]
_drop_spec = ""
_pos = []
_i = 0
while _i < len(_args):
    if _args[_i] == "--drop" and _i + 1 < len(_args):
        _drop_spec = _args[_i + 1]; _i += 2
    else:
        _pos.append(_args[_i]); _i += 1
DEV = _pos[0] if _pos else "/dev/uio4"
DROP_CODES = parse_drop_codes(_drop_spec)
OFF = 0 if "uio" in DEV else 0x8000_0000

# Registers spaced 16 BYTES apart (the hand-written AXI4-Lite slave only returns correct
# data at 16-byte-aligned offsets on this board).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
HEARTBEAT, LOCK = 0xB0, 0xC0
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0
TICK_NS = 1000.0 / 120.0  # clk_os = 120 MHz oversample/timestamp tick (~8.333 ns)

NAME = {STATUS: "STATUS", EVENT: "EVENT", DATA_HI: "DATA_HI", DATA_LO: "DATA_LO",
        TS_HI: "TS_HI", TS_LO: "TS_LO", POP: "POP", EVENT_COUNT: "EVENT_COUNT",
        NULL_COUNT: "NULL_COUNT", ERROR_COUNT: "ERROR_COUNT", DEBUG: "DEBUG",
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT"}

_watch = {"label": None, "t": 0.0}

def _watchdog():
    warned = False
    while True:
        time.sleep(0.5)
        lbl = _watch["label"]
        if lbl is None:
            warned = False
            continue
        if (time.monotonic() - _watch["t"]) > 2.0 and not warned:
            say("# !! WATCHDOG: blocked >2s in %s" % lbl)
            say("# !! An AXI read is wedging the bus (reset held / overlay-bitstream not "
                "loaded / wrong address). This is the PL/BD side, NOT this Python script.")
            say("# !! Ctrl-C cannot break a wedged AXI load; reload the bitstream+overlay "
                "and re-check s_axi_aresetn / the dcm_locked tie-off.")
            warned = True

def _enter(label):
    _watch["t"] = time.monotonic()
    _watch["label"] = label

def _leave():
    _watch["label"] = None

def rd(o):
    _enter("read %s (0x%02X)" % (NAME.get(o, "?"), o))
    v = struct.unpack("<I", m[o:o + 4])[0]
    _leave()
    return v

def wr(o, v=0):
    _enter("write %s (0x%02X)" % (NAME.get(o, "?"), o))
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)
    _leave()

def read_event():
    ev = rd(EVENT)
    event = ev & 0xFFFF
    flags = (ev >> 16) & 0xFFFF
    data = (rd(DATA_HI) << 32) | rd(DATA_LO)
    ts = (rd(TS_HI) << 32) | rd(TS_LO)
    wr(POP)
    return event, flags, data, ts

def stats_line():
    dbg = rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | line_edges=%d level=%d | hb=%d lock=%d" % (
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT), rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1,
        rd(HEARTBEAT), rd(LOCK) & 1)

def probe():
    """One-time startup read of each register, announced BEFORE each access, then a
    TRUST CHECK on the heartbeat (same CDC path as the counters): if it MOVES the
    readback works and EVENT_COUNT / line_edges can be trusted."""
    say("# --- startup probe (a freeze here names the wedged offset) ---")
    for o in (STATUS, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG):
        say("#   reading %-12s 0x%02X ..." % (NAME[o], o))
        v = rd(o)
        say("#     %-12s = 0x%08X" % (NAME[o], v))
    lock = rd(LOCK) & 1
    hb1 = rd(HEARTBEAT)
    time.sleep(0.05)
    hb2 = rd(HEARTBEAT)
    say("#   MMCM lock (0xC0) = %d   heartbeat (0xB0): %d -> %d (+%d)" % (lock, hb1, hb2, hb2 - hb1))
    if lock != 1:
        say("# --- RED FLAG: MMCM not locked => clk_os is dead; the ADM has no clock. "
            "Fix clocking before anything else. ---")
    elif hb2 != hb1 and hb1 != 0:
        say("# --- TRUST OK: heartbeat moving => AXI counter readback works, so "
            "EVENT_COUNT / line_edges are trustworthy. line_edges=0 just means no signal "
            "at the pin yet -> safe to wire up a real ACLK-Lite source. ---")
    else:
        say("# --- WARNING: MMCM locked but heartbeat STUCK => counter readback broken. ---")
    say("# --- probe complete: AXI reads return, the bus is alive. ---")

say("# opening %s (offset 0x%x) ..." % (DEV, OFF))
fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)
say("# mmap ok (0x1000 bytes). starting watchdog ...")
threading.Thread(target=_watchdog, daemon=True).start()
for _c in DROP_CODES:
    wr(FILTER_CFG, filter_cfg_word(_c))
if DROP_CODES:
    say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in DROP_CODES))

say("# streaming ACLK-Lite events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, OFF))
probe()
say(stats_line())
say("#        ts_ticks    dt_us   event     data               tclk  has_data")

last_ts = None
last_stats = time.monotonic()
try:
    while True:
        if rd(STATUS) & 0x1:                       # empty
            now = time.monotonic()
            if now - last_stats >= 1.0:
                say(stats_line())
                last_stats = now
            time.sleep(0.001)
            continue
        event, flags, data, ts = read_event()
        is_tclk = (flags >> 1) & 1
        has_data = flags & 1
        dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * TICK_NS / 1000.0)
        last_ts = ts
        data_str = "0x%016X" % data if has_data else "       --         "
        say("  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data))
except KeyboardInterrupt:
    say("\n# stopped.")
    say(stats_line())
```

- [ ] **Step 2: Compile-check the reader**

Run: `python -m py_compile deploy/aclk_read.py deploy/tclk_filter.py`
Expected: no output, exit 0 (syntax valid; `tclk_filter` import target exists).

- [ ] **Step 3: Add the deploy mapping in `hw.ps1`**

In `hw.ps1`, find the `$pyMap` hashtable in the `deploy` task (currently):

```powershell
        $pyMap = @{
            "tclk"      = @("tclk_read.py", "tclk_filter.py")
            "uart_echo" = @("uart_echo_test.py")
        }
```

Change it to add the `aclk` entry:

```powershell
        $pyMap = @{
            "tclk"      = @("tclk_read.py", "tclk_filter.py")
            "aclk"      = @("aclk_read.py", "tclk_filter.py")
            "uart_echo" = @("uart_echo_test.py")
        }
```

- [ ] **Step 4: Create the runbook**

Create `deploy/aclk.md` with this content:

```markdown
# ACLK-Lite readout on the KR260

The ACLK-Lite (Manchester ADM) readout. Same shared timestamp/FIFO/AXI readout as
the TCLK build, just fed by the Manchester decoder. The TCLK build (deploy/tclk.md)
is unchanged; this is the parallel ACLK target.

## Build (PC, Vivado 2024.2)

    .\hw.ps1 build -Tcl vivado\build_aclk.tcl -Name aclk

Produces `build/kria/aclk/aclk.runs/impl_1/uart_echo_bd_wrapper.bit.bin` (+ MD5 in the
build output and `build-manifest.json`). design_name=uart_echo_bd, so the bitstream
name and the overlay are identical to the TCLK build.

## Deploy (scp the bin + readers)

    .\hw.ps1 deploy -Name aclk -DeployHost ubuntu@kria

Copies `uart_echo_bd_wrapper.bit.bin`, `aclk_read.py`, and `tclk_filter.py` to ~ on the
board.

## Load (on the board)

    md5sum ~/uart_echo_bd_wrapper.bit.bin     # must equal the PC-side MD5
    sudo xmutil unloadapp
    sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo

## Read events

    sudo python3 -u aclk_read.py /dev/uio4

The startup probe reports MMCM lock + a heartbeat trust check. Then each decoded event
prints `ts_ticks dt_us event data tclk has_data` (event is the full 16-bit id; data is
the 64-bit payload when has_data=1; tclk=1 marks a legacy 8-bit event). Drop events with
`--drop 09D,016F` (hex codes). line_edges in the stats line climbs whenever the H12
Manchester line toggles.

## Input

The ACLK-Lite Manchester line enters on H12 (PMOD1 pin 1, LVCMOS33), the same pin the
TCLK build uses (ACLK-Lite rides the same TTL interface). OVERSAMPLE=12 and the 120 MHz
oversample clock assume a ~10 MHz line bit rate; both are tunable in
`rtl/aclk_readout_bd_top.v` / `vivado/build_aclk.tcl` and must be reconciled against a
live source. No real ACLK-Lite transmitter is wired yet, so end-to-end decode is
verified once a source is connected; until then the build, AXI bus, clocking, and
line-activity diagnostic are the verifiable parts.
```

- [ ] **Step 5: Commit**

```bash
git add deploy/aclk_read.py deploy/aclk.md hw.ps1
git commit -m "feat(aclk): PS reader (aclk_read.py), runbook, and hw.ps1 deploy mapping"
```

---

## Self-Review

**Spec coverage:**
- Decoder = ADM (`aclk_lite_decoder`): used via `aclk_lite_readout_top` in Tasks 2-3. Covered.
- Separate `build_aclk.tcl`, TCLK untouched: Task 3; Global Constraints forbid editing TCLK files; Step 3 diffs against the TCLK build. Covered.
- External H12 input only: Task 3 XDC + build port `aclk`. Covered.
- Shared readout, 16-byte map, DROP_NULL=1, event filter: reused unchanged (Global Constraints); reader uses the same offsets. Covered.
- Single MMCM oversample clock + all bring-up fixes (MMCM reset, dcm_locked high, async groups, SmartConnect, 16-byte map, mmcm_locked->0xC0): Task 3. Covered.
- Top parity (mmcm_locked, dbg_hb, DEBUG activity word): Task 1. Covered.
- New files (bd_top, build_aclk.tcl, kr260_aclk.xdc, aclk_read.py, aclk.md) + hw.ps1 edit: Tasks 2-4. Covered.
- Testing: extend `tb/aclk_lite_readout` (Task 1), iverilog elaboration (Task 2), structural diff + optional Vivado build (Task 3), py_compile (Task 4). HW end-to-end deferred (documented in aclk.md). Covered.

**Placeholder scan:** No TBD/TODO; every file has complete content; every step has exact commands and expected output.

**Type/name consistency:** New top ports `mmcm_locked` (input) + `dbg_hb` (output) defined in Task 1, consumed by `aclk_readout_bd_top` in Task 2 with matching names; BD top ports (`clk_os, rstn, aclk, mmcm_locked, clkos_dbg, clk100_dbg, cdc_dbg, dbg_hb`, `S_AXI`) match the `connect_bd_*` / `create_bd_port` calls in Task 3; register offsets in `aclk_read.py` match the shared map. `OVERSAMPLE=12` consistent between bd_top and the build comment. Consistent.
