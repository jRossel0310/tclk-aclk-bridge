# TCLK Board Bring-up Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Get a real Fermilab TCLK signal decoding on the KR260 and stream the decoded events (code + hardware timestamp + counts) to the PS terminal.

**Architecture:** Reuse the sim-validated `tclk_readout_top` (TCLK_RCV + adapter + AXI readout). Add a raw-TCLK activity diagnostic register, wrap it in a plain-Verilog BD top with `X_INTERFACE` attributes, build a Vivado BD (PS three-clock 100/80/40 + `apply_bd_automation` + `dcm_locked` high) that puts the slave at `0x8000_0000` reusing the existing overlay, and read it over UIO with a Python script.

**Tech Stack:** SystemVerilog/Verilog, Icarus + cocotb 2.0 (sim), Vivado 2024.2 (KR260, `xck26-sfvc784-2LV-c`), Python 3 + mmap/UIO (PS).

**Spec:** `docs/superpowers/specs/2026-06-12-tclk-board-bringup-design.md`

**Branch:** work directly on `main` (per user; no branch).

---

## File Structure

| File | Action | Responsibility |
|------|--------|----------------|
| `rtl/aclk_readout/aclk_readout_axi.sv` | Modify | Add generic `dbg_word` input + `0x28 DEBUG` RO register |
| `rtl/aclk_lite/aclk_lite_readout_top.sv` | Modify | Tie `dbg_word=0` (ACLK/Manchester path has no debug word) |
| `tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv` | Modify | Tie `dbg_word=0` |
| `rtl/aclk_lite/tclk_readout_top.sv` | Modify | Add raw-TCLK transition counter + level/sig_err sync -> `dbg_word` |
| `tb/tclk_readout/test_tclk_readout.py` | Modify | New cocotb test for the DEBUG register |
| `rtl/tclk_readout_bd_top.v` | Create | Plain-Verilog BD wrapper with `X_INTERFACE` attributes |
| `constraints/kr260_tclk.xdc` | Create | `tclk` -> package pin H12, LVCMOS33 |
| `vivado/build_tclk.tcl` | Create | Vivado BD + synth/impl/bitstream |
| `deploy/tclk_read.py` | Create | UIO drain loop, stream events to terminal |
| `deploy/tclk.md` | Create | Build / load / run / wiring instructions |

---

### Task 1: Add the generic DEBUG register to the readout AXI slave

Adds a read-only `0x28` register backed by a new `dbg_word[31:0]` input, and ties it
off (0) in every existing instantiation so behavior is unchanged. Pure additive
regression task.

**Files:**
- Modify: `rtl/aclk_readout/aclk_readout_axi.sv`
- Modify: `rtl/aclk_lite/aclk_lite_readout_top.sv`
- Modify: `tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv`
- Modify: `rtl/aclk_lite/tclk_readout_top.sv`

- [ ] **Step 1: Add the `dbg_word` input port to `aclk_readout_axi.sv`**

In the port list, the event-input side ends with `output logic dropped_null,`. Change:

```systemverilog
    output logic         dropped_null,     // debug passthrough
```
to:
```systemverilog
    output logic         dropped_null,     // debug passthrough
    input  logic [31:0]  dbg_word,         // RO debug word -> 0x28 (AXI-domain, caller-synced)
```

- [ ] **Step 2: Map `dbg_word` into the read decode**

In the AXI read channel `case (rsel)`, after the `'d9: rdata_r <= error_count;` line and
before `default:`, add:

```systemverilog
                'd10: rdata_r <= dbg_word;
```

- [ ] **Step 3: Tie `dbg_word=0` in the three existing instantiations**

In `rtl/aclk_lite/aclk_lite_readout_top.sv`, in the `u_axi` instance, change:
```systemverilog
        .dropped_null  (dropped_null),
```
to:
```systemverilog
        .dropped_null  (dropped_null),
        .dbg_word      (32'd0),
```

In `tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv`, in the `u_axi` instance, change:
```systemverilog
        .dropped_null  (dropped_null),
```
to:
```systemverilog
        .dropped_null  (dropped_null),
        .dbg_word      (32'd0),
```

In `rtl/aclk_lite/tclk_readout_top.sv`, in the `u_axi` instance, change:
```systemverilog
        .dropped_null  (dropped_null),
```
to:
```systemverilog
        .dropped_null  (dropped_null),
        .dbg_word      (32'd0),
```

- [ ] **Step 4: Run the full sim regression, expect all green**

Run:
```powershell
.\sim.ps1 run -Module aclk_readout_axi
.\sim.ps1 run -Module aclk_lite_readout
.\sim.ps1 run -Module tclk_readout
.\sim.ps1 run -Module aclk_readout
```
Expected: each ends with `TESTS=... PASS=... FAIL=0`. The DEBUG register reads 0
everywhere (no test reads it yet), so nothing changes behaviorally.

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_readout/aclk_readout_axi.sv rtl/aclk_lite/aclk_lite_readout_top.sv tb/aclk_readout_axi/tb_aclk_readout_axi_top.sv rtl/aclk_lite/tclk_readout_top.sv
git commit -m "Add generic 0x28 DEBUG register to aclk_readout_axi (tied 0)"
```

---

### Task 2: TCLK activity counter feeding the DEBUG register (TDD)

Counts raw transitions on the H12 line (80 MHz oversample), crosses the count to the
AXI domain, and packs `{sig_err, level, transitions[29:0]}` into `dbg_word`. This is
the on-board signal-presence diagnostic.

**Files:**
- Modify: `rtl/aclk_lite/tclk_readout_top.sv`
- Test: `tb/tclk_readout/test_tclk_readout.py`

- [ ] **Step 1: Write the failing test**

In `tb/tclk_readout/test_tclk_readout.py`, add the `DEBUG` offset to the register
constants block (it currently ends at `ERROR_COUNT`). Change:
```python
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT = (
    0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24
)
```
to:
```python
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28
)
```
Then append this new test at the end of the file:
```python
@cocotb.test()
async def test_tclk_debug_activity(dut):
    """The 0x28 DEBUG register's transition counter climbs while the TCLK line
    toggles; the level/sig_err bits read back. This is the on-board signal-presence
    diagnostic: transitions climbing but EVENT_COUNT flat => signal present, decoder
    not locking; transitions flat => no signal / pin / front-end."""
    _start_clocks(dut)
    await reset_dut(dut)

    events = [0x9D, 0xD2, 0x07]
    acct = {"w": 0, "read": 0}
    cocotb.start_soon(_tclk_driver(dut, events, acct))

    await _wait_flag(dut, acct, "warm_done")
    await ClockCycles(dut.s_axi_aclk, 8)
    d0 = await axi_read(dut, DEBUG)
    c0 = d0 & 0x3FFFFFFF

    await _wait_flag(dut, acct, "drive_done", step=8)
    await ClockCycles(dut.s_axi_aclk, 8)
    d1 = await axi_read(dut, DEBUG)
    c1 = d1 & 0x3FFFFFFF
    acct["stop_drive"] = True

    assert c1 > c0, f"DEBUG transition count did not climb: c0={c0} c1={c1}"
    sig_err = (d1 >> 31) & 1
    level = (d1 >> 30) & 1
    assert sig_err in (0, 1) and level in (0, 1), f"DEBUG flag bits unreadable: 0x{d1:08X}"
    dut._log.info(f"DEBUG OK: transitions {c0} -> {c1}, level={level}, sig_err={sig_err}")
```

- [ ] **Step 2: Run the test, expect it to fail**

Run: `.\sim.ps1 run -Module tclk_readout`
Expected: `test_tclk_debug_activity` FAILS with `DEBUG transition count did not climb: c0=0 c1=0` (dbg_word is still tied 0 from Task 1).

- [ ] **Step 3: Implement the activity counter in `tclk_readout_top.sv`**

In `rtl/aclk_lite/tclk_readout_top.sv`, after the `perr_pulse` logic and before the
`// ---- adapter: TCLK_RCV -> readout ----` comment, insert:

```systemverilog
    // ---- raw-TCLK activity diagnostic (-> DEBUG register 0x28) ----
    // 2-FF synchronize the raw line into clk_80m (80 MHz oversamples the <=20 MHz
    // biphase edge rate), count every transition, and cross the count to the AXI
    // domain with the same Gray-coded counter the readout uses elsewhere.
    logic tclk_m, tclk_s, tclk_s_d;
    always_ff @(posedge clk_80m or negedge rstn) begin
        if (!rstn) begin
            tclk_m   <= 1'b0;
            tclk_s   <= 1'b0;
            tclk_s_d <= 1'b0;
        end else begin
            tclk_m   <= tclk;
            tclk_s   <= tclk_m;
            tclk_s_d <= tclk_s;
        end
    end
    wire tclk_edge = tclk_s ^ tclk_s_d;          // one clk_80m pulse per transition

    wire [29:0] edge_count;
    cdc_gray_count #(.W(30)) u_cnt_edge (
        .src_clk(clk_80m), .src_rstn(rstn), .incr(tclk_edge),
        .dst_clk(s_axi_aclk), .count_dst(edge_count));

    // Live level + serdec carrier error, synchronized into the AXI domain.
    logic lvl_m, lvl_s, serr_m, serr_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            lvl_m  <= 1'b0; lvl_s  <= 1'b0;
            serr_m <= 1'b0; serr_s <= 1'b0;
        end else begin
            lvl_m  <= tclk;    lvl_s  <= lvl_m;
            serr_m <= sig_err; serr_s <= serr_m;
        end
    end

    wire [31:0] tclk_dbg_word = {serr_s, lvl_s, edge_count};
```

Then change the readout instance tie-off from Task 1:
```systemverilog
        .dbg_word      (32'd0),
```
to:
```systemverilog
        .dbg_word      (tclk_dbg_word),
```

- [ ] **Step 4: Run the test, expect pass**

Run: `.\sim.ps1 run -Module tclk_readout`
Expected: `TESTS=3 PASS=3 FAIL=0` (the two original tests plus `test_tclk_debug_activity`).

- [ ] **Step 5: Commit**

```bash
git add rtl/aclk_lite/tclk_readout_top.sv tb/tclk_readout/test_tclk_readout.py
git commit -m "Add raw-TCLK transition counter to tclk_readout_top DEBUG word"
```

---

### Task 3: Plain-Verilog BD wrapper with X_INTERFACE attributes

`tclk_readout_top` is SystemVerilog; Vivado wants a plain-Verilog module-reference top
for the BD (same reason `uart_echo_bd_top.v` exists), and the `s_axi_*` ports need
`X_INTERFACE` attributes so `apply_bd_automation` recognizes the AXI4-Lite slave.

**Files:**
- Create: `rtl/tclk_readout_bd_top.v`

- [ ] **Step 1: Create `rtl/tclk_readout_bd_top.v`**

```verilog
// rtl/tclk_readout_bd_top.v
//
// Plain-Verilog block-design wrapper around tclk_readout_top (SystemVerilog). The
// X_INTERFACE attributes let Vivado infer the AXI4-Lite slave (S_AXI) and its
// clock/reset association, so apply_bd_automation can wire the PS LPD master to it.
// pps is tied 0 (no White Rabbit yet); the discrete dbg_* outputs are unused on the
// board (debug is read via the 0x28 DEBUG register over AXI).

`timescale 1ns / 1ps

module tclk_readout_bd_top (
    // receive domain (connected to PS pl_clk1/pl_clk2 + reset in the BD)
    input  wire        clk_80m,
    input  wire        clk_40m,
    input  wire        rstn,
    input  wire        tclk,

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

    tclk_readout_top #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) u_tclk (
        .clk_80m       (clk_80m),
        .clk_40m       (clk_40m),
        .rstn          (rstn),
        .pps           (1'b0),
        .tclk          (tclk),

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

        .dbg_dav       (),
        .dbg_data      (),
        .dbg_perr      (),
        .dbg_sig_err   (),
        .dropped_null  ()
    );

endmodule
```

- [ ] **Step 2: Syntax-check with iverilog (X_INTERFACE attrs are ignored by iverilog)**

Run:
```powershell
$env:PATH = "$env:OSS_CAD_SUITE\bin;$env:OSS_CAD_SUITE\lib;$env:PATH"
iverilog -g2012 -o $env:TEMP\tclkbd.out rtl\tclk_readout_bd_top.v rtl\aclk_lite\tclk_readout_top.sv rtl\aclk_readout\aclk_readout_axi.sv rtl\aclk_readout\aclk_readout_core.sv rtl\async_fifo.sv rtl\synchronizer.sv rtl\cdc_gray_count.sv rtl\aclk_bridge\serdec4_9MHz.v rtl\aclk_bridge\TCLK_DESERIALIZER2.v rtl\aclk_bridge\TCLK_RCV.v
```
Expected: no errors (a clean elaboration of `tclk_readout_bd_top`). Warnings about
unused `X_INTERFACE` attributes are fine.

- [ ] **Step 3: Commit**

```bash
git add rtl/tclk_readout_bd_top.v
git commit -m "Add plain-Verilog BD wrapper for tclk_readout_top (X_INTERFACE)"
```

---

### Task 4: H12 input constraint

**Files:**
- Create: `constraints/kr260_tclk.xdc`

- [ ] **Step 1: Create `constraints/kr260_tclk.xdc`**

```tcl
## constraints/kr260_tclk.xdc - real TCLK input
##
## The biphase-mark TCLK line (3.3V baseband from the external front-end) enters
## on KR260 PMOD1 pin 1 = package H12, LVCMOS33. Verify the physical connector
## position against the carrier-card silkscreen; the package pin (H12) is correct.

set_property -dict {PACKAGE_PIN H12 IOSTANDARD LVCMOS33} [get_ports tclk]
```

- [ ] **Step 2: Commit**

```bash
git add constraints/kr260_tclk.xdc
git commit -m "Add H12 TCLK input constraint"
```

---

### Task 5: Vivado build script for the TCLK design

**Files:**
- Create: `vivado/build_tclk.tcl`

- [ ] **Step 1: Create `vivado/build_tclk.tcl`**

```tcl
# vivado/build_tclk.tcl — real-TCLK readout on the KR260.
#
# PS (three PL clocks: 100 AXI, 80 serdec, 40 deserializer/readout) + the TCLK
# readout (tclk_readout_bd_top) on the LPD AXI master at 0x8000_0000. The TCLK line
# enters on H12. Reuses design_name=uart_echo_bd so the bitstream is named
# uart_echo_bd_wrapper.bit.bin and the existing overlay loads unchanged.
#
# Build:  vivado -mode batch -source vivado/build_tclk.tcl
#    or:  .\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk   (PS, with AV retry)

set proj_name   tclk
set design_name uart_echo_bd
set part        xck26-sfvc784-2LV-c

set script_dir [file dirname [file normalize [info script]]]
set root_dir   [file dirname $script_dir]
set rtl_dir    [file join $root_dir rtl]
set xdc_file   [file join $root_dir constraints kr260_tclk.xdc]

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

# RTL sources: readout chain + TCLK decoder + tclk_readout_top + the Verilog BD top.
add_files -norecurse [list \
    [file join $rtl_dir synchronizer.sv] \
    [file join $rtl_dir async_fifo.sv] \
    [file join $rtl_dir cdc_gray_count.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_core.sv] \
    [file join $rtl_dir aclk_readout aclk_readout_axi.sv] \
    [file join $rtl_dir aclk_bridge serdec4_9MHz.v] \
    [file join $rtl_dir aclk_bridge TCLK_DESERIALIZER2.v] \
    [file join $rtl_dir aclk_bridge TCLK_RCV.v] \
    [file join $rtl_dir aclk_lite tclk_readout_top.sv] \
    [file join $rtl_dir tclk_readout_bd_top.v] \
]
set_property file_type SystemVerilog [get_files *.sv]
add_files -fileset constrs_1 -norecurse [list $xdc_file]
update_compile_order -fileset sources_1

create_bd_design $design_name

# Zynq US+ PS with board preset; three PL clocks + the LPD AXI master.
set ps [create_bd_cell -type ip -vlnv xilinx.com:ip:zynq_ultra_ps_e:* zynq_ultra_ps_e_0]
if {[llength $bp] > 0} {
    apply_bd_automation -rule xilinx.com:bd_rule:zynq_ultra_ps_e -config {apply_board_preset 1} $ps
}
set_property -dict [list \
    CONFIG.PSU__FPGA_PL0_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL0_REF_CTRL__FREQMHZ {100} \
    CONFIG.PSU__FPGA_PL1_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL1_REF_CTRL__FREQMHZ {80} \
    CONFIG.PSU__FPGA_PL2_ENABLE {1} \
    CONFIG.PSU__CRL_APB__PL2_REF_CTRL__FREQMHZ {40} \
    CONFIG.PSU__USE__M_AXI_GP0 {0} \
    CONFIG.PSU__USE__M_AXI_GP1 {0} \
    CONFIG.PSU__USE__M_AXI_GP2 {1} \
] $ps

# Our TCLK readout as a module reference (the Verilog BD wrapper).
set tclk [create_bd_cell -type module -reference tclk_readout_bd_top u_tclk]

# PS -> our AXI slave over LPD; automation builds interconnect + reset + clock +
# address, and connects s_axi_aclk/s_axi_aresetn via the inferred S_AXI interface.
apply_bd_automation -rule xilinx.com:bd_rule:axi4 -config { \
    Master      {/zynq_ultra_ps_e_0/M_AXI_HPM0_LPD} \
    Clk_xbar    {Auto} \
    Clk_master  {Auto} \
    Clk_slave   {Auto} \
    intc_ip     {Auto} \
    master_apm  {0} \
} [get_bd_intf_pins u_tclk/S_AXI]

# Tie the auto-reset's dcm_locked high so the design isn't held in reset.
set rst [lindex [get_bd_cells -filter {VLNV =~ "*proc_sys_reset*"}] 0]
set vcc [create_bd_cell -type ip -vlnv xilinx.com:ip:xlconstant:* dcm_locked_const]
set_property -dict [list CONFIG.CONST_WIDTH {1} CONFIG.CONST_VAL {1}] $vcc
connect_bd_net [get_bd_pins $vcc/dout] [get_bd_pins $rst/dcm_locked]

# Receive-domain clocks + reset.
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk1] [get_bd_pins u_tclk/clk_80m]
connect_bd_net [get_bd_pins zynq_ultra_ps_e_0/pl_clk2] [get_bd_pins u_tclk/clk_40m]
connect_bd_net [get_bd_pins $rst/peripheral_aresetn]   [get_bd_pins u_tclk/rstn]

# External TCLK input -> H12 (constrained in the XDC).
create_bd_port -dir I tclk
connect_bd_net [get_bd_port tclk] [get_bd_pins u_tclk/tclk]

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
    puts "ERROR: bitstream not found — check the impl_1 run log."
    exit 1
}
```

- [ ] **Step 2: Commit**

```bash
git add vivado/build_tclk.tcl
git commit -m "Add Vivado build for the real-TCLK readout BD"
```

---

### Task 6: PS readout script

**Files:**
- Create: `deploy/tclk_read.py`

- [ ] **Step 1: Create `deploy/tclk_read.py`**

```python
#!/usr/bin/env python3
"""Stream decoded TCLK events from the PL readout over UIO.

Drains the AXI-Lite readout at 0x8000_0000: polls STATUS, reads each buffered event
(code + flags + 64-bit hardware timestamp), pops it, prints a line. Every ~1 s prints
a stats line: EVENT/NULL/ERROR counts + the DEBUG activity register (raw TCLK
transitions, which climb even if the decoder never locks).

    sudo python3 tclk_read.py /dev/uio4

Ctrl-C to stop. Diagnostic reading: tclk_edges climbing + EVT flat => signal present
but not decoding; tclk_edges flat => no signal / pin / front-end.
"""
import mmap, os, struct, sys, time

DEV = sys.argv[1] if len(sys.argv) > 1 else "/dev/uio4"
OFF = 0 if "uio" in DEV else 0x8000_0000

STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x04, 0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C, 0x20, 0x24, 0x28
)
TICK_NS = 25.0  # clk_40m = 40 MHz timestamp tick

fd = os.open(DEV, os.O_RDWR | os.O_SYNC)
m = mmap.mmap(fd, 0x1000, mmap.MAP_SHARED, mmap.PROT_READ | mmap.PROT_WRITE, offset=OFF)

def rd(o):
    return struct.unpack("<I", m[o:o + 4])[0]

def wr(o, v=0):
    m[o:o + 4] = struct.pack("<I", v & 0xFFFFFFFF)

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
    return "[stats] EVT=%d NULL=%d ERR=%d | tclk_edges=%d level=%d sig_err=%d" % (
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1)

print("# streaming TCLK events from %s (offset 0x%x). Ctrl-C to stop." % (DEV, OFF))
print(stats_line())
print("#        ts_ticks    dt_us   event  tclk  has_data")

last_ts = None
last_stats = time.monotonic()
try:
    while True:
        if rd(STATUS) & 0x1:                       # empty
            now = time.monotonic()
            if now - last_stats >= 1.0:
                print(stats_line())
                last_stats = now
            time.sleep(0.001)
            continue
        event, flags, data, ts = read_event()
        is_tclk = (flags >> 1) & 1
        has_data = flags & 1
        dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * TICK_NS / 1000.0)
        last_ts = ts
        print("  %16d %s   0x%02X    %d      %d" % (ts, dt, event & 0xFF, is_tclk, has_data))
except KeyboardInterrupt:
    print("\n# stopped.")
    print(stats_line())
```

- [ ] **Step 2: Commit**

```bash
git add deploy/tclk_read.py
git commit -m "Add PS UIO TCLK event-stream reader"
```

---

### Task 7: Deploy / run documentation

**Files:**
- Create: `deploy/tclk.md`

- [ ] **Step 1: Create `deploy/tclk.md`**

````markdown
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
````

- [ ] **Step 2: Commit**

```bash
git add deploy/tclk.md
git commit -m "Add TCLK board deploy/run doc"
```

---

### Task 8: Build, deploy, and first light (manual / hardware)

No automated test — this is on-board verification. Do NOT commit anything; this task
only produces a bitstream (git-ignored) and runtime output.

- [ ] **Step 1: Build the bitstream**

Run: `.\hw.ps1 build -Tcl vivado\build_tclk.tcl -Name tclk`
Expected: ends with `BITSTREAM: …uart_echo_bd_wrapper.bit`. If `apply_bd_automation`
fails to find the `S_AXI` interface (X_INTERFACE not recognized), STOP and fall back
to packaging `tclk_readout_top` as a Vivado IP (spec "Risks"); re-plan that task.
Confirm in the impl log that the slave is assigned base `0x8000_0000`.

- [ ] **Step 2: bootgen -> .bit.bin** (Vivado/Vitis shell, in the impl_1 folder with `uart_echo.bif`)

Run: `bootgen -arch zynqmp -process_bitstream bin -image uart_echo.bif`
Expected: `uart_echo_bd_wrapper.bit.bin` produced.

- [ ] **Step 3: Copy + load on the board**

```powershell
scp "$env:USERPROFILE\kria-builds\tclk\tclk.runs\impl_1\uart_echo_bd_wrapper.bit.bin" deploy\tclk_read.py "ubuntu@[fe80::48ec:6a99:b6fd:80e9%6]:~"
```
Then on the board:
```bash
sudo xmutil unloadapp
sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -f Full
```
Expected: load succeeds, no errors.

- [ ] **Step 4: First light**

Run on the board: `sudo python3 tclk_read.py /dev/uioN` (find N via `ls -l /dev/uio*`).
Expected (success): real TCLK event codes scroll with monotonically increasing
timestamps, `[stats]` shows EVT climbing and `tclk_edges` climbing. Use the
diagnosing table in `deploy/tclk.md` if not.

---

## Self-Review

**Spec coverage:**
- DEBUG register (spec component 1) -> Task 1. Activity counter (component 2) -> Task 2.
  BD wrapper (component 3) -> Task 3. XDC (component 4) -> Task 4. build_tclk.tcl
  (component 5) -> Task 5. tclk_read.py (component 6) -> Task 6. tclk.md (component 7)
  -> Task 7. Build/deploy/first-light -> Task 8. Clocking (PS 100/80/40) -> Task 5.
  Reset (peripheral_aresetn -> rstn, dcm_locked high) -> Task 5. Register map 0x28 ->
  Tasks 1-2. Re-run all four sims -> Tasks 1, 2. All spec sections covered.

**Type/name consistency:**
- `dbg_word` (input on `aclk_readout_axi`, source `tclk_dbg_word` in `tclk_readout_top`),
  `0x28`/`DEBUG`, bit layout `{sig_err[31], level[30], transitions[29:0]}` consistent
  across `aclk_readout_axi.sv`, `tclk_readout_top.sv`, the cocotb test, and
  `tclk_read.py`. `cdc_gray_count #(.W(30))` matches the 30-bit `edge_count`.
  Cell/port names (`u_tclk/clk_80m`, `clk_40m`, `rstn`, `tclk`, `S_AXI`) match between
  `tclk_readout_bd_top.v` and `build_tclk.tcl`. `pl_clk1=80`, `pl_clk2=40` consistent.

**Placeholder scan:** none — every step has concrete code/commands.

**Notes / risks (from spec):** X_INTERFACE inference is the riskiest step; Task 8
Step 1 has the explicit fallback (package as IP). 80/40 MHz exactness: Vivado reports
achieved frequencies in the build log; verify they read 80.000 / 40.000.
