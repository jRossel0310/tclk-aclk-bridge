# White Rabbit Disciplined Timestamp (sec:ns) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the pipeline's free-running tick timestamps with a White Rabbit disciplined `{sec[31:0], ns[31:0]}` timebase (Unix UTC seconds : nanoseconds), locked to a 10 MHz + PPS pair on Pmod 1 and coarse-set from the NTP-synced PS clock.

**Architecture:** A new `wr_timebase` module is replicated per event clock domain (TCLK `clk_40m`, ACLK `rx_usrclk2`, plus a monitor copy in `s_axi_aclk`); every instance watches the same two physical pins so all copies carry one timeline with no 64-bit CDC. ns = (10 MHz edges since PPS) * 100 plus a local-clock fixed-point interpolator cleared at every edge. STRICT validity: `ts` is 64'd0 unless armed + PPS-loaded + both watchdogs alive. A new small AXI-Lite slave `wr_timebase_axi` (bus S_AXI3 at 0x8002_0000) owns arm/status registers. Spec: `docs/superpowers/specs/2026-07-06-wr-timestamp-design.md`.

**Tech Stack:** SystemVerilog (Icarus-compatible), cocotb 2.0 via `tb/runner_common.py`, Vivado block-design tcl, Python UIO tooling in `deploy/`.

## Global Constraints

- Never use em dashes anywhere (code, comments, docs, commit messages). Use commas, colons, or parentheses.
- AXI registers are spaced 16 BYTES apart (the 16-byte aliasing lesson); register select is `addr[7:4]`.
- The AXI write channel must accept AW and W independently (the proven no-deadlock pattern in `rtl/aclk_readout/aclk_readout_axi.sv:236-284`); never require AWVALID and WVALID in the same cycle.
- New RTL uses `always_ff` with async active-low resets, the project `synchronizer` for 2-FF CDC, and must compile under Icarus (`.\sim.ps1 run -Module <name>` uses SIM=icarus by default).
- Every cocotb suite emits at least one matplotlib plot on completion (repo convention), guarded by try/except so a missing matplotlib only warns.
- Simulations use a SHORTENED second: 50 WR cells = 5000 ns per "second" (real hardware: 10,000,000 cells). Watchdog parameters scale down to match; production values live only in `rtl/aclk_pipeline_bd_top.v`.
- Run suites with `.\sim.ps1 run -Module <name>` (PowerShell) from the repo root.
- Commit after every task with the message given in the task.
- The packed event word `{FLAGS[15:0], TS[63:0], EVENT[15:0], DATA[63:0]}` and the two existing readout register maps do not change.

---

### Task 1: `cdc_word_pulse` CDC primitive

**Files:**
- Create: `rtl/cdc_word_pulse.sv`
- Create: `tb/cdc_word_pulse/runner.py`
- Create: `tb/cdc_word_pulse/test_cdc_word_pulse.py`

**Interfaces:**
- Consumes: `rtl/synchronizer.sv` (`synchronizer #(.WIDTH(1), .STAGES(2)) (.clk, .async_signal, .sync_signal)`).
- Produces: `module cdc_word_pulse #(parameter int W = 32) (input src_clk, src_rstn, src_valid, src_data[W-1:0], dst_clk, dst_rstn, output dst_valid, dst_data[W-1:0])`. `dst_valid` is a 1-cycle strobe; `dst_data` is valid with it. Task 2's `wr_timebase` instantiates it with `W=33`.

- [ ] **Step 1: Write the failing test**

Create `tb/cdc_word_pulse/runner.py`:

```python
"""Cocotb 2.0 runner for rtl/cdc_word_pulse.sv (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_cdc_word_pulse():
    run_cocotb(
        "cdc_word_pulse",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_word_pulse.sv",
        ],
        hdl_toplevel="cdc_word_pulse",
    )


if __name__ == "__main__":
    test_cdc_word_pulse()
```

Create `tb/cdc_word_pulse/test_cdc_word_pulse.py`:

```python
"""Toggle-handshake word CDC: each src_valid delivers exactly one dst_valid with
the captured word; a dst-domain reset must NOT replay a stale transfer."""
import warnings
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from cocotb_helpers import _b, start_clock


def _start_clocks(dut):
    # cocotb kills a test's spawned tasks (including clock drivers) when the
    # test ends, so EVERY test must start its own clocks.
    start_clock(dut.src_clk, 10)   # 100 MHz source
    start_clock(dut.dst_clk, 25)   # 40 MHz destination (slower, worst case)


async def _reset(dut):
    dut.src_rstn.value = 0
    dut.dst_rstn.value = 0
    dut.src_valid.value = 0
    dut.src_data.value = 0
    await ClockCycles(dut.dst_clk, 4)
    await Timer(1, unit="ns")
    dut.src_rstn.value = 1
    dut.dst_rstn.value = 1
    await ClockCycles(dut.dst_clk, 6)   # warmup counter flush


async def _send(dut, value):
    await RisingEdge(dut.src_clk)
    dut.src_data.value = value
    dut.src_valid.value = 1
    await RisingEdge(dut.src_clk)
    dut.src_valid.value = 0


async def _collect(dut, cycles):
    """Sample dst_valid for `cycles` dst clocks; return list of received words."""
    got = []
    for _ in range(cycles):
        await RisingEdge(dut.dst_clk)
        await Timer(1, unit="ns")
        if _b(dut.dst_valid) == 1:
            got.append(int(dut.dst_data.value))
    return got


@cocotb.test()
async def test_single_and_repeated_transfers(dut):
    _start_clocks(dut)
    await _reset(dut)

    valid_levels = []

    await _send(dut, 0xDEADBEEF)
    got = await _collect(dut, 12)
    assert got == [0xDEADBEEF], f"expected one delivery of 0xDEADBEEF, got {got}"

    # spaced transfers (>= 3 dst clocks apart) all arrive, once each
    sent = [0x11111111, 0x22222222, 0x33333333]
    got = []
    for v in sent:
        await _send(dut, v)
        got += await _collect(dut, 12)
        for _ in range(3):
            await RisingEdge(dut.dst_clk)
            await Timer(1, unit="ns")
            valid_levels.append(_b(dut.dst_valid))
    assert got == sent, f"expected {sent}, got {got}"

    _save_plot(valid_levels)


@cocotb.test()
async def test_dst_reset_does_not_replay(dut):
    _start_clocks(dut)
    await _reset(dut)

    # One real transfer flips the toggle to 1.
    await _send(dut, 0xCAFED00D)
    got = await _collect(dut, 12)
    assert got == [0xCAFED00D]

    # Reset ONLY the destination (models a GT relock resetting rx domain logic).
    dut.dst_rstn.value = 0
    await ClockCycles(dut.dst_clk, 3)
    await Timer(1, unit="ns")
    dut.dst_rstn.value = 1

    # No new src_valid: the stale toggle level must NOT fire dst_valid again.
    got = await _collect(dut, 20)
    assert got == [], f"stale transfer replayed after dst reset: {got}"

    # A fresh transfer still works.
    await _send(dut, 0x55AA55AA)
    got = await _collect(dut, 12)
    assert got == [0x55AA55AA]


def _save_plot(valid_levels):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    xs = list(range(len(valid_levels)))
    fig, ax = plt.subplots(figsize=(9, 2.5))
    ax.step(xs, valid_levels, where="post", color="tab:blue", lw=1.4)
    ax.set_ylim(-0.2, 1.2)
    ax.set_yticks([0, 1])
    ax.set_xlabel("dst_clk sample")
    ax.set_ylabel("dst_valid")
    ax.set_title("cdc_word_pulse: dst_valid strobes between transfers")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "cdc_word_pulse" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "dst_valid.png", dpi=120)
    plt.close(fig)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\sim.ps1 run -Module cdc_word_pulse`
Expected: FAIL at build (Icarus cannot find `rtl/cdc_word_pulse.sv` / module `cdc_word_pulse` undefined).

- [ ] **Step 3: Write the implementation**

Create `rtl/cdc_word_pulse.sv`:

```systemverilog
// rtl/cdc_word_pulse.sv
//
// Single-outstanding word-plus-pulse clock-domain crossing (toggle handshake).
// On src_valid the word is captured and a request toggle flips on the SAME src
// edge; the destination 2-FF-syncs the toggle and, on any change, emits a
// 1-cycle dst_valid with the word. Because data_q is stable from the same edge
// the toggle flips and the synced toggle arrives >= 2 dst_clk later, sampling
// data_q in the destination domain is skew-safe by protocol.
//
// Warmup: after a destination-domain reset the current toggle level is adopted
// WITHOUT firing, so a reset while the toggle sits at 1 cannot replay a stale
// transfer (e.g. re-arming the WR timebase with an old seconds value after a
// GT relock).
//
// Contract: src_valid pulses must be spaced >= 3 dst_clk periods (plus 2
// src_clk) apart or a transfer is silently lost. Fine for quasi-static config
// like the WR seconds arm, which software writes at human timescales.

`timescale 1ns / 1ps

module cdc_word_pulse #(
    parameter int W = 32
) (
    input  logic         src_clk,
    input  logic         src_rstn,     // async, active-low
    input  logic         src_valid,    // 1-cycle strobe
    input  logic [W-1:0] src_data,
    input  logic         dst_clk,
    input  logic         dst_rstn,     // async, active-low
    output logic         dst_valid,    // 1-cycle strobe
    output logic [W-1:0] dst_data
);

    // ---- source side: capture the word and flip the request toggle ----
    logic         req_tgl;
    logic [W-1:0] data_q;
    always_ff @(posedge src_clk or negedge src_rstn) begin
        if (!src_rstn) begin
            req_tgl <= 1'b0;
            data_q  <= '0;
        end else if (src_valid) begin
            req_tgl <= ~req_tgl;
            data_q  <= src_data;
        end
    end

    // ---- destination side: sync the toggle, fire on change, sample the word ----
    wire tgl_sync;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync (
        .clk          (dst_clk),
        .async_signal (req_tgl),
        .sync_signal  (tgl_sync)
    );

    logic [1:0] warmup;
    logic       tgl_d;
    always_ff @(posedge dst_clk or negedge dst_rstn) begin
        if (!dst_rstn) begin
            warmup    <= 2'd0;
            tgl_d     <= 1'b0;
            dst_valid <= 1'b0;
            dst_data  <= '0;
        end else if (warmup != 2'd3) begin
            // adopt the current toggle level without firing
            warmup    <= warmup + 2'd1;
            tgl_d     <= tgl_sync;
            dst_valid <= 1'b0;
        end else begin
            tgl_d     <= tgl_sync;
            dst_valid <= (tgl_sync != tgl_d);
            if (tgl_sync != tgl_d) dst_data <= data_q;
        end
    end

endmodule
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\sim.ps1 run -Module cdc_word_pulse`
Expected: PASS (2 tests), plot written to `sim_build/cdc_word_pulse/plots/dst_valid.png`.

- [ ] **Step 5: Commit**

```bash
git add rtl/cdc_word_pulse.sv tb/cdc_word_pulse/
git commit -m "feat(rtl): cdc_word_pulse toggle-handshake word CDC (arm-value crossing for WR timebase)"
```

---

### Task 2: `wr_timebase` core + WR stimulus model

**Files:**
- Create: `rtl/wr_timebase.sv`
- Create: `tb/wr_model.py` (shared WR 10 MHz + PPS stimulus)
- Create: `tb/wr_timebase/runner.py`
- Create: `tb/wr_timebase/tb_wr_timebase_top.sv`
- Create: `tb/wr_timebase/test_wr_timebase.py`

**Interfaces:**
- Consumes: `synchronizer` and `cdc_word_pulse` (Task 1, `W=33` instance: `src_data = {cfg_disarm, cfg_sec}`).
- Produces (used verbatim by Tasks 3, 4, 5):

```systemverilog
module wr_timebase #(
    parameter int unsigned CLK_PERIOD_DS = 250,        // local clock period, 0.1 ns units
    parameter int unsigned CLK10_TIMEOUT = 16,         // cycles w/o a 10 MHz edge -> unlock
    parameter int unsigned PPS_TIMEOUT   = 44_000_000  // cycles w/o a PPS edge -> unlock
) (
    input  logic clk, rstn, wr_clk10, wr_pps,
    input  logic cfg_clk, cfg_rstn, cfg_valid, cfg_disarm,
    input  logic [31:0] cfg_sec,
    output logic [63:0] ts,            // {sec, ns}; 64'd0 unless locked
    output logic locked, arm_pending, pps_alive, clk10_alive, pps_edge,
    output logic [31:0] cells_last
);
```

- Also produces `tb/wr_model.py` class `WrGen(clk10_sig, pps_sig, cells_per_second=50, pps_high_cells=5)` with `.start()`, `.stop()`, gates `.clk10_on` / `.pps_on`, and `.pps_times_ns` (list of PPS rising-edge sim times). Task 3 and Task 4 reuse it.

- [ ] **Step 1: Write the WR stimulus model**

Create `tb/wr_model.py`:

```python
"""White Rabbit stimulus model: a 10 MHz cell clock and a phase-aligned PPS.

The 'second' is SHORTENED for simulation: every `cells_per_second` wr_clk10
rising edges (default 50, i.e. 5000 ns) one PPS pulse rises aligned with the
cell edge and stays high for `pps_high_cells` cells. DUT watchdog parameters
are scaled down to match (see the suite runners).

Gates model line faults: set `clk10_on` / `pps_on` False (and drive the signal
low from the test) to simulate a dead line; set back True to restore it.
"""
import cocotb
from cocotb.triggers import Timer
from cocotb.utils import get_sim_time


class WrGen:
    CELL_NS = 100          # 10 MHz: one cell per 100 ns

    def __init__(self, clk10_sig, pps_sig, cells_per_second=50, pps_high_cells=5):
        self.clk10 = clk10_sig
        self.pps = pps_sig
        self.cps = cells_per_second
        self.high = pps_high_cells
        self.clk10_on = True
        self.pps_on = True
        self.pps_times_ns = []
        self._task = None

    def start(self):
        self.clk10.value = 0
        self.pps.value = 0
        self._task = cocotb.start_soon(self._drive())

    def stop(self):
        if self._task is not None:
            self._task.kill()
            self._task = None
        self.clk10.value = 0
        self.pps.value = 0

    async def _drive(self):
        cell = 0
        while True:
            if cell == 0 and self.pps_on:
                self.pps.value = 1
                self.pps_times_ns.append(get_sim_time(unit="ns"))
            if cell == self.high:
                self.pps.value = 0
            if self.clk10_on:
                self.clk10.value = 1
            await Timer(self.CELL_NS // 2, unit="ns")
            if self.clk10_on:
                self.clk10.value = 0
            await Timer(self.CELL_NS // 2, unit="ns")
            cell = (cell + 1) % self.cps
```

- [ ] **Step 2: Write the failing test**

Create `tb/wr_timebase/tb_wr_timebase_top.sv` (two instances: an integer-period 40 MHz domain and a fractional-period 6.4 ns domain, proving the interpolator generalizes; both watch the same pins, so this tb also proves cross-domain agreement):

```systemverilog
// tb/wr_timebase/tb_wr_timebase_top.sv
//
// Two wr_timebase replicas on one WR pin pair:
//   u_a: 25.0 ns local clock (clk_40m-like, integer ns period)
//   u_b:  6.4 ns local clock (fractional period, exercises the 0.1 ns
//         interpolator remainder path; no production domain needs it today)
// Sim-scaled watchdogs for a 50-cell (5 us) 'second':
//   CLK10_TIMEOUT ~= 400 ns of cycles, PPS_TIMEOUT ~= 6 us of cycles.

`timescale 1ns / 1ps

module tb_wr_timebase_top (
    input  wire        clk_a,        // 25 ns
    input  wire        clk_b,        // 6.4 ns
    input  wire        cfg_clk,      // 10 ns (AXI-like)
    input  wire        rstn,
    input  wire        cfg_rstn,
    input  wire        wr_clk10,
    input  wire        wr_pps,
    input  wire        cfg_valid,
    input  wire        cfg_disarm,
    input  wire [31:0] cfg_sec,

    output wire [63:0] ts_a,
    output wire        locked_a,
    output wire        arm_pending_a,
    output wire        pps_alive_a,
    output wire        clk10_alive_a,
    output wire        pps_edge_a,
    output wire [31:0] cells_last_a,

    output wire [63:0] ts_b,
    output wire        locked_b
);

    wr_timebase #(
        .CLK_PERIOD_DS (250),
        .CLK10_TIMEOUT (16),     // 400 ns at 40 MHz
        .PPS_TIMEOUT   (240)     // 6 us at 40 MHz
    ) u_a (
        .clk(clk_a), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(cfg_clk), .cfg_rstn(cfg_rstn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_a), .locked(locked_a), .arm_pending(arm_pending_a),
        .pps_alive(pps_alive_a), .clk10_alive(clk10_alive_a),
        .pps_edge(pps_edge_a), .cells_last(cells_last_a)
    );

    wr_timebase #(
        .CLK_PERIOD_DS (64),
        .CLK10_TIMEOUT (63),     // ~403 ns at 156.25 MHz
        .PPS_TIMEOUT   (940)     // ~6 us at 156.25 MHz
    ) u_b (
        .clk(clk_b), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(cfg_clk), .cfg_rstn(cfg_rstn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_b), .locked(locked_b), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

endmodule
```

Create `tb/wr_timebase/runner.py`:

```python
"""Cocotb 2.0 runner for rtl/wr_timebase.sv (shared plumbing: tb/runner_common.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_wr_timebase():
    run_cocotb(
        "wr_timebase",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_word_pulse.sv",
            "rtl/wr_timebase.sv",
            "tb/wr_timebase/tb_wr_timebase_top.sv",
        ],
        hdl_toplevel="tb_wr_timebase_top",
    )


if __name__ == "__main__":
    test_wr_timebase()
```

Create `tb/wr_timebase/test_wr_timebase.py`:

```python
"""wr_timebase: strict-zero before arm, arm-and-lock at PPS, ns tracks the WR
cells with local interpolation, both replicas agree, loss of either reference
unlocks strictly (re-arm required), disarm unlocks immediately.

Sim second = 50 cells = 5000 ns (see tb/wr_model.py)."""
import warnings
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge, ClockCycles, Timer
from cocotb.utils import get_sim_time

from cocotb_helpers import _b, start_clock
from wr_model import WrGen

SIM_NS_PER_SEC = 5000     # 50 cells * 100 ns
SEC0 = 1_751_800_000      # arbitrary Unix-like armed seconds value


def _start_clocks(dut):
    # cocotb kills a test's spawned tasks (including clock drivers) when the
    # test ends, so EVERY test must start its own clocks.
    start_clock(dut.clk_a, 25)
    cocotb.start_soon(Clock(dut.clk_b, 6400, unit="ps").start())
    start_clock(dut.cfg_clk, 10)


async def _reset(dut):
    dut.rstn.value = 0
    dut.cfg_rstn.value = 0
    dut.cfg_valid.value = 0
    dut.cfg_disarm.value = 0
    dut.cfg_sec.value = 0
    dut.wr_clk10.value = 0
    dut.wr_pps.value = 0
    await ClockCycles(dut.cfg_clk, 10)
    await Timer(1, unit="ns")
    dut.rstn.value = 1
    dut.cfg_rstn.value = 1
    await ClockCycles(dut.cfg_clk, 10)


async def _arm(dut, sec):
    await RisingEdge(dut.cfg_clk)
    dut.cfg_sec.value = sec
    dut.cfg_disarm.value = 0
    dut.cfg_valid.value = 1
    await RisingEdge(dut.cfg_clk)
    dut.cfg_valid.value = 0


async def _disarm(dut):
    await RisingEdge(dut.cfg_clk)
    dut.cfg_disarm.value = 1
    dut.cfg_valid.value = 1
    await RisingEdge(dut.cfg_clk)
    dut.cfg_valid.value = 0
    dut.cfg_disarm.value = 0


def _split(ts):
    return (ts >> 32) & 0xFFFFFFFF, ts & 0xFFFFFFFF


def _combined(ts):
    """Sim-timeline value in ns since second SEC0 (sim second = 5000 ns)."""
    sec, ns = _split(ts)
    return (sec - SEC0) * SIM_NS_PER_SEC + ns


async def _wait_locked(dut, timeout_sim_seconds=3):
    for _ in range(timeout_sim_seconds * SIM_NS_PER_SEC // 25):
        await RisingEdge(dut.clk_a)
        await Timer(1, unit="ns")
        if _b(dut.locked_a) == 1 and _b(dut.locked_b) == 1:
            return
    raise AssertionError("timebase never locked after arm")


@cocotb.test()
async def test_strict_arm_lock_and_tracking(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()

    # -- strict zero before arm: run 3 sim-seconds, ts stays 0 --
    for _ in range(30):
        await ClockCycles(dut.clk_a, 20)
        await Timer(1, unit="ns")
        assert _b(dut.ts_a) == 0, "ts_a nonzero before arm"
        assert _b(dut.ts_b) == 0, "ts_b nonzero before arm"
        assert _b(dut.locked_a) == 0 and _b(dut.locked_b) == 0
    # references are alive and the interval cell count is measured anyway
    assert _b(dut.pps_alive_a) == 1 and _b(dut.clk10_alive_a) == 1
    assert _b(dut.cells_last_a) == 50, (
        f"cells_last {_b(dut.cells_last_a)} != 50")

    # -- arm mid-second, lock at the next PPS with the armed seconds --
    await _arm(dut, SEC0)
    await Timer(1, unit="ns")
    await _wait_locked(dut)
    sec_a, ns_a = _split(_b(dut.ts_a))
    assert sec_a == SEC0, f"sec {sec_a} != armed {SEC0}"
    assert ns_a < SIM_NS_PER_SEC, f"ns {ns_a} out of range"
    assert _b(dut.arm_pending_a) == 0, "arm still pending after lock"

    # -- seconds self-increment at each PPS --
    n_pps = len(gen.pps_times_ns)
    while len(gen.pps_times_ns) < n_pps + 2:
        await ClockCycles(dut.clk_a, 20)
    await ClockCycles(dut.clk_a, 8)   # let the edge cross the 2-FF sync
    await Timer(1, unit="ns")
    sec_a2, _ = _split(_b(dut.ts_a))
    assert sec_a2 >= SEC0 + 2, f"sec did not increment: {sec_a2}"

    # -- ns tracks WR truth, both domains agree; collect samples for the plot --
    samples = []   # (sim_ns, expected_ns, ns_a, ns_b, comb_a, comb_b)
    for i in range(200):
        await ClockCycles(dut.clk_a, 7 + (i % 5))
        await Timer(1, unit="ns")
        now = get_sim_time(unit="ns")
        expected = now - gen.pps_times_ns[-1]
        ts_a, ts_b = _b(dut.ts_a), _b(dut.ts_b)
        _, ns_a = _split(ts_a)
        _, ns_b = _split(ts_b)
        ca, cb = _combined(ts_a), _combined(ts_b)
        # DUT lags real time by 2-FF sync + edge detect; never leads by > 2 clk.
        # Skip samples within a sync window of the PPS (sec/ns straddle there).
        if expected > 200:
            assert ns_a <= expected + 50,  f"ns_a {ns_a} leads wall time {expected}"
            assert ns_a >= expected - 250, f"ns_a {ns_a} lags too far ({expected})"
            assert abs(ca - cb) <= 150, (
                f"domains disagree: a={ca} b={cb} (delta {ca - cb})")
        samples.append((now, expected, ns_a, ns_b, ca, cb))

    _save_plot(samples)
    gen.stop()


@cocotb.test()
async def test_reference_loss_unlocks_strictly(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()
    await ClockCycles(dut.clk_a, 300)
    await _arm(dut, SEC0)
    await _wait_locked(dut)

    # -- 10 MHz dies: unlock within CLK10_TIMEOUT (16 clk_a = 400 ns) + margin --
    gen.clk10_on = False
    dut.wr_clk10.value = 0
    await ClockCycles(dut.clk_a, 30)
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0 and _b(dut.ts_a) == 0, "clk10 loss did not unlock"

    # -- restoring the reference does NOT relock (strict: re-arm required) --
    gen.clk10_on = True
    await ClockCycles(dut.clk_a, 3 * SIM_NS_PER_SEC // 25)
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0, "relocked without a fresh arm (strict violated)"
    await _arm(dut, SEC0 + 100)
    await _wait_locked(dut)
    sec_a, _ = _split(_b(dut.ts_a))
    assert sec_a >= SEC0 + 100

    # -- PPS dies: unlock after PPS_TIMEOUT (240 clk_a = 6 us) --
    gen.pps_on = False
    await ClockCycles(dut.clk_a, 300)
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0 and _b(dut.ts_a) == 0, "PPS loss did not unlock"
    gen.pps_on = True
    await _arm(dut, SEC0 + 200)
    await _wait_locked(dut)
    gen.stop()


@cocotb.test()
async def test_disarm_unlocks(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()
    await ClockCycles(dut.clk_a, 300)
    await _arm(dut, SEC0)
    await _wait_locked(dut)

    await _disarm(dut)
    await ClockCycles(dut.clk_a, 12)   # cfg CDC + a couple of clk_a
    await Timer(1, unit="ns")
    assert _b(dut.locked_a) == 0 and _b(dut.ts_a) == 0, "disarm did not unlock"
    assert _b(dut.locked_b) == 0 and _b(dut.ts_b) == 0
    gen.stop()


def _save_plot(samples):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    t   = [s[0] for s in samples]
    exp = [s[1] for s in samples]
    na  = [s[2] for s in samples]
    nb  = [s[3] for s in samples]
    fig, ax = plt.subplots(figsize=(11, 4))
    ax.plot(t, exp, color="tab:gray",   lw=1.0, label="wall-clock ns since PPS")
    ax.plot(t, na,  color="tab:blue",   lw=1.4, label="ns_a (25 ns domain)")
    ax.plot(t, nb,  color="tab:orange", lw=1.4, linestyle="--",
            label="ns_b (6.4 ns domain)")
    ax.set_xlabel("sim time (ns)")
    ax.set_ylabel("ns field")
    ax.set_title("wr_timebase: ns tracks the WR cells in both domains")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "wr_timebase" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "ns_tracking.png", dpi=120)
    plt.close(fig)
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `.\sim.ps1 run -Module wr_timebase`
Expected: FAIL at build (module `wr_timebase` undefined).

- [ ] **Step 4: Write the implementation**

Create `rtl/wr_timebase.sv`:

```systemverilog
// rtl/wr_timebase.sv
//
// White-Rabbit-disciplined {sec[31:0], ns[31:0]} timebase, generated INSIDE the
// consumer's clock domain. Instantiate one per event domain: every instance
// watches the same two physical pins, so all copies carry the same timeline and
// no 64-bit value ever crosses a clock domain.
//
// ns construction: each detected wr_clk10 rising edge adds 100 ns (one WR
// cell); between edges a fixed-point interpolator adds the local clock period
// (CLK_PERIOD_DS, 0.1 ns units, exact for 25.0 / 16.0 / 10.0 / 6.4 ns), and is
// cleared at every edge so interpolation error never accumulates. A PPS rising
// edge zeroes ns, latches the interval cell count, and loads or increments sec.
//
// STRICT validity: ts reads 64'd0 unless locked. Locked requires a seconds
// value armed through the cfg interface AND loaded at a PPS edge, AND both
// watchdogs alive (a wr_clk10 edge within CLK10_TIMEOUT cycles, a PPS within
// PPS_TIMEOUT cycles). Any violation, or a disarm request, unlocks; re-locking
// requires a fresh arm. ts == 0 therefore means "not WR-synced when stamped".
//
// The cfg_* interface lives in its own clock domain (the AXI clock) and crosses
// in through one cdc_word_pulse, so the AXI slave drives many instances with
// the same wires. cfg_valid strobes with cfg_disarm=0 arm cfg_sec (the Unix UTC
// label of the NEXT PPS); with cfg_disarm=1 they force an unlock.

`timescale 1ns / 1ps

module wr_timebase #(
    parameter int unsigned CLK_PERIOD_DS = 250,        // local clock period, 0.1 ns units
    parameter int unsigned CLK10_TIMEOUT = 16,         // cycles w/o a 10 MHz edge -> unlock
    parameter int unsigned PPS_TIMEOUT   = 44_000_000  // cycles w/o a PPS edge -> unlock
) (
    input  logic        clk,
    input  logic        rstn,             // async, active-low
    input  logic        wr_clk10,         // async WR 10 MHz reference (Pmod pin)
    input  logic        wr_pps,           // async WR pulse-per-second (Pmod pin)
    // ---- cfg domain (AXI clock): arm / disarm, CDC'd in internally ----
    input  logic        cfg_clk,
    input  logic        cfg_rstn,
    input  logic        cfg_valid,        // 1-cycle strobe
    input  logic        cfg_disarm,       // qualifies cfg_valid: 1 = disarm
    input  logic [31:0] cfg_sec,          // Unix UTC label of the NEXT PPS
    // ---- outputs (clk domain) ----
    output logic [63:0] ts,               // {sec, ns}; forced to 0 unless locked
    output logic        locked,
    output logic        arm_pending,
    output logic        pps_alive,
    output logic        clk10_alive,
    output logic        pps_edge,         // 1-cycle strobe per PPS rising edge
    output logic [31:0] cells_last        // wr_clk10 cells in the previous PPS interval
);

    localparam int unsigned PERIOD_NS_INT = CLK_PERIOD_DS / 10;
    // whole-ns and 0.1 ns parts of the local period; FRAC is 0..9 so 4 bits hold it
    localparam logic [3:0] PERIOD_FRAC = CLK_PERIOD_DS % 10;

    // ---- 2-FF sync + rising-edge detect for the WR pins ----
    wire clk10_s, pps_s;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_clk10 (
        .clk(clk), .async_signal(wr_clk10), .sync_signal(clk10_s));
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_pps (
        .clk(clk), .async_signal(wr_pps), .sync_signal(pps_s));

    logic clk10_d, pps_d;
    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            clk10_d <= 1'b0;
            pps_d   <= 1'b0;
        end else begin
            clk10_d <= clk10_s;
            pps_d   <= pps_s;
        end
    end
    wire clk10_re = clk10_s & ~clk10_d;
    wire pps_re   = pps_s   & ~pps_d;
    assign pps_edge = pps_re;

    // ---- arm / disarm from the cfg domain (one toggle-handshake CDC) ----
    wire        req_v;
    wire [32:0] req_w;
    cdc_word_pulse #(.W(33)) u_cfg_cdc (
        .src_clk(cfg_clk), .src_rstn(cfg_rstn),
        .src_valid(cfg_valid), .src_data({cfg_disarm, cfg_sec}),
        .dst_clk(clk), .dst_rstn(rstn),
        .dst_valid(req_v), .dst_data(req_w));
    wire        req_is_disarm = req_w[32];
    wire [31:0] req_sec       = req_w[31:0];

    // ---- watchdogs: saturating, reset to expired so alive starts low ----
    logic [31:0] clk10_wd, pps_wd;
    assign clk10_alive = (clk10_wd < CLK10_TIMEOUT);
    assign pps_alive   = (pps_wd   < PPS_TIMEOUT);

    // The WR PPS is phase-aligned to a wr_clk10 edge, and two independent 2-FF
    // syncs can resolve that shared physical edge one cycle apart. Suppress
    // wr_clk10 edges for 2 cycles after each PPS so the boundary edge cannot
    // double-count as "cell 1" right after ns was zeroed (2 cycles is well
    // under one 100 ns cell in every target domain: 50 ns at 40 MHz).
    logic [1:0] pps_shadow;
    wire clk10_cell = clk10_re && (pps_shadow == 2'd0) && !pps_re;

    // ---- the timebase proper ----
    logic        armed;
    logic [31:0] arm_sec_q;
    logic [31:0] sec;
    logic [31:0] ns_base;      // ns at the last accepted wr_clk10 edge (multiple of 100)
    logic [31:0] interp;       // whole ns accumulated since that edge
    logic [3:0]  interp_frac;  // 0.1 ns remainder of the accumulation
    logic [31:0] cells;
    logic        lk;

    wire [4:0] frac_next  = {1'b0, interp_frac} + {1'b0, PERIOD_FRAC};
    wire       frac_carry = (frac_next >= 5'd10);
    wire [4:0] frac_adj   = frac_next - 5'd10;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            armed       <= 1'b0;
            arm_sec_q   <= '0;
            sec         <= '0;
            ns_base     <= '0;
            interp      <= '0;
            interp_frac <= '0;
            cells       <= '0;
            cells_last  <= '0;
            lk          <= 1'b0;
            pps_shadow  <= 2'd0;
            clk10_wd    <= CLK10_TIMEOUT;   // start expired: not alive until edges arrive
            pps_wd      <= PPS_TIMEOUT;
        end else begin
            // Arm / disarm requests. If an arm lands on the same cycle as a PPS
            // edge consuming a PREVIOUS arm, the PPS branch below wins for
            // `armed` and the new arm is lost; software arms mid-second (see
            // deploy/wr_time.py), so that coincidence cannot happen in practice.
            if (req_v) begin
                if (req_is_disarm) begin
                    armed <= 1'b0;
                    lk    <= 1'b0;
                end else begin
                    armed     <= 1'b1;
                    arm_sec_q <= req_sec;
                end
            end

            // Watchdogs: cleared by their edge, saturate at the timeout.
            if (clk10_re)                      clk10_wd <= '0;
            else if (clk10_wd < CLK10_TIMEOUT) clk10_wd <= clk10_wd + 1'b1;
            if (pps_re)                        pps_wd   <= '0;
            else if (pps_wd < PPS_TIMEOUT)     pps_wd   <= pps_wd + 1'b1;

            // STRICT: a dead reference unlocks; re-locking needs a fresh arm.
            if (!clk10_alive || !pps_alive) lk <= 1'b0;

            if (pps_shadow != 2'd0) pps_shadow <= pps_shadow - 2'd1;

            if (pps_re) begin
                // PPS boundary: zero ns, latch the interval cell count, seconds.
                ns_base     <= '0;
                interp      <= '0;
                interp_frac <= '0;
                cells_last  <= cells;
                cells       <= '0;
                pps_shadow  <= 2'd2;
                if (armed) begin
                    sec   <= arm_sec_q;
                    armed <= 1'b0;
                    if (clk10_alive) lk <= 1'b1;   // never lock onto a dead 10 MHz
                end else if (lk) begin
                    sec <= sec + 1'b1;
                end
            end else if (clk10_cell) begin
                // WR cell edge: snap ns to cells*100, restart interpolation.
                ns_base     <= ns_base + 32'd100;
                interp      <= '0;
                interp_frac <= '0;
                cells       <= cells + 1'b1;
            end else begin
                // Free-run interpolation between edges, exact in 0.1 ns units.
                interp      <= interp + PERIOD_NS_INT + (frac_carry ? 32'd1 : 32'd0);
                interp_frac <= frac_carry ? frac_adj[3:0] : frac_next[3:0];
            end
        end
    end

    // ns saturates just below 1 s so a late PPS cannot produce a malformed
    // field in the window before the PPS watchdog unlocks.
    wire [31:0] ns_raw = ns_base + interp;
    wire [31:0] ns_sat = (ns_raw > 32'd999_999_999) ? 32'd999_999_999 : ns_raw;

    assign ts          = lk ? {sec, ns_sat} : 64'd0;
    assign locked      = lk;
    assign arm_pending = armed;

endmodule
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `.\sim.ps1 run -Module wr_timebase`
Expected: PASS (3 tests), plot at `sim_build/wr_timebase/plots/ns_tracking.png`. If the tracking tolerances trip, inspect the waveform (`.\sim.ps1 wave -Module wr_timebase`) before loosening anything: the DUT should lag wall time by roughly 3 local clocks and never lead it.

Note on the spec's "full-length spot checks": a true 1 s interval is 40M clk_a cycles, impractical under Icarus. The substitute is (a) the multi-sim-second tracking run above (the accumulate/snap/zero machinery exercises identically at 50 cells per second) and (b) width checks by inspection: `ns_base` is 32-bit and holds 999,999,900, `cells` is 32-bit and holds 10,000,000, `pps_wd` is 32-bit and holds 110,000,000. The spec records this substitution.

- [ ] **Step 6: Run the Task 1 suite to catch regressions**

Run: `.\sim.ps1 run -Module cdc_word_pulse`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add rtl/wr_timebase.sv tb/wr_model.py tb/wr_timebase/
git commit -m "feat(rtl): wr_timebase WR-disciplined sec:ns timebase (strict validity, per-domain replica)"
```

---

### Task 3: `wr_timebase_axi` register slave

**Files:**
- Create: `rtl/wr_timebase_axi.sv`
- Create: `tb/wr_timebase_axi/runner.py`
- Create: `tb/wr_timebase_axi/test_wr_timebase_axi.py`

**Interfaces:**
- Consumes: `wr_timebase` (Task 2), `synchronizer`, `tb/axi_lite_bfm.py` (`axi_read(dut, addr, pfx="")`, `axi_write(dut, addr, data, pfx="")`), `tb/wr_model.py` (`WrGen`).
- Produces (used verbatim by Tasks 4 and 5):

```systemverilog
module wr_timebase_axi #(
    parameter int AXI_ADDR_W = 8,
    parameter int unsigned MON_CLK_PERIOD_DS = 100,
    parameter int unsigned MON_CLK10_TIMEOUT = 40,
    parameter int unsigned MON_PPS_TIMEOUT   = 110_000_000
) (
    input  logic wr_clk10, wr_pps,
    input  logic locked_a, locked_b,          // replica lock status (async)
    output logic cfg_valid, cfg_disarm,       // s_axi_aclk-domain strobes
    output logic [31:0] cfg_sec,
    // + the standard s_axi_* AXI4-Lite slave port set (as in aclk_readout_axi)
);
```

Register map (16-byte stride):

| Offset | Name | Access | Contents |
|---|---|---|---|
| 0x00 | STATUS | RO | [0] locked_tclk (locked_a synced), [1] locked_aclk (locked_b synced), [2] locked_mon, [3] pps_alive, [4] clk10_alive, [5] arm_pending, [8] lost_lock sticky |
| 0x10 | SEC_ARM | RW | write arms the value as the next-PPS Unix UTC label; reads back |
| 0x20 | SEC_NOW | RO | monitor seconds; the read atomically latches NS_NOW |
| 0x30 | NS_NOW | RO | ns latched by the last SEC_NOW read |
| 0x40 | PPS_COUNT | RO | PPS edges seen since reset |
| 0x50 | CELLS_LAST | RO | 10 MHz cells in the last PPS interval (hardware expects 10,000,000) |
| 0x60 | CTRL | WO | [0] clear lost_lock sticky, [1] broadcast disarm |

- [ ] **Step 1: Write the failing test**

Create `tb/wr_timebase_axi/runner.py`:

```python
"""Cocotb 2.0 runner for rtl/wr_timebase_axi.sv (shared plumbing: tb/runner_common.py).
Monitor watchdogs are sim-scaled: 'second' = 50 cells = 5 us."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_wr_timebase_axi():
    run_cocotb(
        "wr_timebase_axi",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_word_pulse.sv",
            "rtl/wr_timebase.sv",
            "rtl/wr_timebase_axi.sv",
        ],
        hdl_toplevel="wr_timebase_axi",
        parameters={
            "MON_CLK10_TIMEOUT": 40,    # 400 ns at 100 MHz
            "MON_PPS_TIMEOUT":   600,   # 6 us at 100 MHz
        },
    )


if __name__ == "__main__":
    test_wr_timebase_axi()
```

Create `tb/wr_timebase_axi/test_wr_timebase_axi.py`:

```python
"""wr_timebase_axi: STATUS bits, SEC_ARM write arms + locks the monitor at PPS,
atomic SEC_NOW/NS_NOW latch, PPS_COUNT / CELLS_LAST diagnostics, CTRL disarm +
sticky lost_lock semantics. Sim second = 50 cells = 5000 ns."""
import warnings
from pathlib import Path

import cocotb
from cocotb.triggers import RisingEdge, ClockCycles, Timer

from cocotb_helpers import _b, start_clock
from axi_lite_bfm import axi_read, axi_write
from wr_model import WrGen

STATUS, SEC_ARM, SEC_NOW, NS_NOW, PPS_COUNT, CELLS_LAST, CTRL = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60)

SIM_NS_PER_SEC = 5000
SEC0 = 1_751_800_000


def _start_clocks(dut):
    # cocotb kills a test's spawned tasks (including clock drivers) when the
    # test ends, so EVERY test must start its own clocks.
    start_clock(dut.s_axi_aclk, 10)


async def _reset(dut):
    dut.s_axi_aresetn.value = 0
    dut.locked_a.value = 0
    dut.locked_b.value = 0
    dut.wr_clk10.value = 0
    dut.wr_pps.value = 0
    for sig in ("awaddr", "awvalid", "wdata", "wstrb", "wvalid",
                "bready", "araddr", "arvalid", "rready"):
        getattr(dut, "s_axi_" + sig).value = 0
    await ClockCycles(dut.s_axi_aclk, 10)
    await Timer(1, unit="ns")
    dut.s_axi_aresetn.value = 1
    await ClockCycles(dut.s_axi_aclk, 10)


async def _wait_status_bit(dut, bit, value, timeout_cycles=3000):
    for _ in range(timeout_cycles // 10):
        s = await axi_read(dut, STATUS)
        if ((s >> bit) & 1) == value:
            return s
        await ClockCycles(dut.s_axi_aclk, 10)
    raise AssertionError(f"STATUS bit {bit} never became {value}")


@cocotb.test()
async def test_status_and_diag_counters(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()

    # replica lock inputs surface as STATUS[1:0]
    s = await axi_read(dut, STATUS)
    assert (s & 0x3) == 0
    dut.locked_a.value = 1
    dut.locked_b.value = 1
    await ClockCycles(dut.s_axi_aclk, 5)
    s = await axi_read(dut, STATUS)
    assert (s & 0x3) == 0x3, f"replica lock bits missing: 0x{s:08X}"
    dut.locked_a.value = 0
    dut.locked_b.value = 0

    # aliveness + interval measurement come up on their own (no arm needed)
    s = await _wait_status_bit(dut, 3, 1)          # pps_alive
    assert (s >> 4) & 1 == 1, "clk10_alive not set"
    await ClockCycles(dut.s_axi_aclk, 2 * SIM_NS_PER_SEC // 10)
    cells = await axi_read(dut, CELLS_LAST)
    assert cells == 50, f"CELLS_LAST {cells} != 50"
    c1 = await axi_read(dut, PPS_COUNT)
    await ClockCycles(dut.s_axi_aclk, SIM_NS_PER_SEC // 10 + 50)
    c2 = await axi_read(dut, PPS_COUNT)
    assert c2 > c1, f"PPS_COUNT stuck at {c1}"
    gen.stop()


@cocotb.test()
async def test_arm_now_regs_and_sticky(dut):
    _start_clocks(dut)
    await _reset(dut)
    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()
    await ClockCycles(dut.s_axi_aclk, 100)

    # before arm: strict zeros
    assert await axi_read(dut, SEC_NOW) == 0
    assert await axi_read(dut, NS_NOW) == 0

    # arm: readback + arm_pending, then the monitor locks at the next PPS
    await axi_write(dut, SEC_ARM, SEC0)
    assert await axi_read(dut, SEC_ARM) == SEC0
    s = await axi_read(dut, STATUS)
    assert (s >> 5) & 1 == 1, "arm_pending not set after SEC_ARM write"
    s = await _wait_status_bit(dut, 2, 1)          # locked_mon

    # SEC_NOW/NS_NOW: sec is the armed label (+ elapsed sim-seconds), the
    # NS_NOW read returns the value latched AT the SEC_NOW read (atomic pair).
    samples = []
    for _ in range(20):
        sec = await axi_read(dut, SEC_NOW)
        ns = await axi_read(dut, NS_NOW)
        assert sec >= SEC0, f"SEC_NOW {sec} < armed {SEC0}"
        assert ns < SIM_NS_PER_SEC, f"NS_NOW {ns} >= sim second"
        samples.append((sec - SEC0) * SIM_NS_PER_SEC + ns)
        await ClockCycles(dut.s_axi_aclk, 37)
    assert samples == sorted(samples), f"combined time not monotonic: {samples}"

    # CTRL[1] disarm: monitor unlocks, sticky sets; CTRL[0] clears the sticky
    await axi_write(dut, CTRL, 0x2)
    s = await _wait_status_bit(dut, 2, 0)
    assert (s >> 8) & 1 == 1, f"lost_lock sticky not set: 0x{s:08X}"
    assert await axi_read(dut, SEC_NOW) == 0, "SEC_NOW nonzero after disarm"
    await axi_write(dut, CTRL, 0x1)
    s = await axi_read(dut, STATUS)
    assert (s >> 8) & 1 == 0, "lost_lock sticky did not clear"

    # re-arm relocks
    await axi_write(dut, SEC_ARM, SEC0 + 500)
    await _wait_status_bit(dut, 2, 1)

    _save_plot(samples)
    gen.stop()


def _save_plot(samples):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    xs = list(range(len(samples)))
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.step(xs, samples, where="post", color="tab:blue", lw=1.6)
    ax.set_xlabel("SEC_NOW/NS_NOW read pair index")
    ax.set_ylabel("combined time since arm (sim ns)")
    ax.set_title("wr_timebase_axi: atomic SEC_NOW/NS_NOW reads are monotonic")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "wr_timebase_axi" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "now_reads.png", dpi=120)
    plt.close(fig)
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `.\sim.ps1 run -Module wr_timebase_axi`
Expected: FAIL at build (module `wr_timebase_axi` undefined).

- [ ] **Step 3: Write the implementation**

Create `rtl/wr_timebase_axi.sv`:

```systemverilog
// rtl/wr_timebase_axi.sv
//
// AXI4-Lite face of the White Rabbit timebase (bus S_AXI3 in the pipeline).
// Contains the MONITOR wr_timebase instance (s_axi_aclk domain) that backs the
// status registers, and fans the arm/disarm cfg strobes out to the event-domain
// replicas (which CDC them in themselves via cdc_word_pulse).
//
// Registers are spaced 16 BYTES apart (project convention; see the aliasing
// note in rtl/aclk_readout/aclk_readout_axi.sv). Register select = addr[7:4].
//
//   0x00 STATUS      RO  [0] locked_tclk  [1] locked_aclk  [2] locked_mon
//                        [3] pps_alive    [4] clk10_alive  [5] arm_pending
//                        [8] lost_lock (sticky, cleared by CTRL[0])
//   0x10 SEC_ARM     RW  Unix UTC label of the NEXT PPS; the write arms
//   0x20 SEC_NOW     RO  monitor seconds; the read atomically latches NS_NOW
//   0x30 NS_NOW      RO  ns latched by the last SEC_NOW read
//   0x40 PPS_COUNT   RO  PPS edges seen since reset
//   0x50 CELLS_LAST  RO  10 MHz cells in the last PPS interval (expect 10,000,000)
//   0x60 CTRL        WO  [0] clear lost_lock  [1] broadcast disarm
//
// STRICT semantics note: SEC_NOW/NS_NOW read 0 while the monitor is unlocked
// (the monitor's ts is forced to 0), so a zero pair means "not synced".

`timescale 1ns / 1ps

module wr_timebase_axi #(
    parameter int AXI_ADDR_W = 8,
    parameter int unsigned MON_CLK_PERIOD_DS = 100,        // s_axi_aclk = 100 MHz
    parameter int unsigned MON_CLK10_TIMEOUT = 40,         // 400 ns at 100 MHz
    parameter int unsigned MON_PPS_TIMEOUT   = 110_000_000 // 1.1 s at 100 MHz
) (
    // ---- WR pins (async) ----
    input  logic        wr_clk10,
    input  logic        wr_pps,
    // ---- event-domain replica lock status (async; 2-FF synced here) ----
    input  logic        locked_a,       // TCLK-domain replica
    input  logic        locked_b,       // ACLK-domain replica
    // ---- cfg fan-out (s_axi_aclk domain; replicas CDC it in themselves) ----
    output logic        cfg_valid,      // 1-cycle strobe
    output logic        cfg_disarm,     // qualifies cfg_valid
    output logic [31:0] cfg_sec,
    // ---- AXI4-Lite slave (PS clock) ----
    input  logic                   s_axi_aclk,
    input  logic                   s_axi_aresetn,
    input  logic [AXI_ADDR_W-1:0]  s_axi_awaddr,
    input  logic                   s_axi_awvalid,
    output logic                   s_axi_awready,
    input  logic [31:0]            s_axi_wdata,
    input  logic [3:0]             s_axi_wstrb,
    input  logic                   s_axi_wvalid,
    output logic                   s_axi_wready,
    output logic [1:0]             s_axi_bresp,
    output logic                   s_axi_bvalid,
    input  logic                   s_axi_bready,
    input  logic [AXI_ADDR_W-1:0]  s_axi_araddr,
    input  logic                   s_axi_arvalid,
    output logic                   s_axi_arready,
    output logic [31:0]            s_axi_rdata,
    output logic [1:0]             s_axi_rresp,
    output logic                   s_axi_rvalid,
    input  logic                   s_axi_rready
);

    // ---------------------------------------------------------------
    // Monitor timebase instance (everything in the s_axi_aclk domain;
    // its internal cfg CDC just adds a few cycles of latency).
    // ---------------------------------------------------------------
    wire [63:0] ts_mon;
    wire        locked_mon, arm_pending_mon, pps_alive_mon, clk10_alive_mon;
    wire        pps_edge_mon;
    wire [31:0] cells_last_mon;

    wr_timebase #(
        .CLK_PERIOD_DS (MON_CLK_PERIOD_DS),
        .CLK10_TIMEOUT (MON_CLK10_TIMEOUT),
        .PPS_TIMEOUT   (MON_PPS_TIMEOUT)
    ) u_mon (
        .clk         (s_axi_aclk),
        .rstn        (s_axi_aresetn),
        .wr_clk10    (wr_clk10),
        .wr_pps      (wr_pps),
        .cfg_clk     (s_axi_aclk),
        .cfg_rstn    (s_axi_aresetn),
        .cfg_valid   (cfg_valid),
        .cfg_disarm  (cfg_disarm),
        .cfg_sec     (cfg_sec),
        .ts          (ts_mon),
        .locked      (locked_mon),
        .arm_pending (arm_pending_mon),
        .pps_alive   (pps_alive_mon),
        .clk10_alive (clk10_alive_mon),
        .pps_edge    (pps_edge_mon),
        .cells_last  (cells_last_mon)
    );

    // ---------------------------------------------------------------
    // Replica lock bits, 2-FF synced into the AXI domain.
    // ---------------------------------------------------------------
    wire locked_a_s, locked_b_s;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_la (
        .clk(s_axi_aclk), .async_signal(locked_a), .sync_signal(locked_a_s));
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_lb (
        .clk(s_axi_aclk), .async_signal(locked_b), .sync_signal(locked_b_s));

    // ---------------------------------------------------------------
    // AXI4-Lite read channel (single outstanding, 16-byte stride).
    // ---------------------------------------------------------------
    logic        arready_r, rvalid_r;
    logic [31:0] rdata_r, ns_latch, sec_arm_reg, pps_count;
    logic        lost_lock;
    wire [AXI_ADDR_W-5:0] rsel = s_axi_araddr[AXI_ADDR_W-1:4];

    wire [31:0] status_word = {23'b0, lost_lock, 2'b0, arm_pending_mon,
                               clk10_alive_mon, pps_alive_mon, locked_mon,
                               locked_b_s, locked_a_s};

    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            arready_r <= 1'b1;
            rvalid_r  <= 1'b0;
            rdata_r   <= 32'b0;
            ns_latch  <= 32'b0;
        end else if (arready_r && s_axi_arvalid) begin
            arready_r <= 1'b0;
            rvalid_r  <= 1'b1;
            case (rsel)
                'd0: rdata_r <= status_word;
                'd1: rdata_r <= sec_arm_reg;
                'd2: begin                               // SEC_NOW latches NS_NOW
                    rdata_r  <= ts_mon[63:32];
                    ns_latch <= ts_mon[31:0];
                end
                'd3: rdata_r <= ns_latch;
                'd4: rdata_r <= pps_count;
                'd5: rdata_r <= cells_last_mon;
                default: rdata_r <= 32'b0;
            endcase
        end else if (rvalid_r && s_axi_rready) begin
            rvalid_r  <= 1'b0;
            arready_r <= 1'b1;
        end
    end

    assign s_axi_arready = arready_r;
    assign s_axi_rvalid  = rvalid_r;
    assign s_axi_rdata   = rdata_r;
    assign s_axi_rresp   = 2'b00;            // OKAY

    // ---------------------------------------------------------------
    // AXI4-Lite write channel: AW and W accepted INDEPENDENTLY (the proven
    // no-deadlock pattern from aclk_readout_axi.sv). Also home of the
    // PPS_COUNT counter and the lost_lock sticky (all s_axi_aclk domain).
    // ---------------------------------------------------------------
    logic awready_r, wready_r, bvalid_r;
    logic [AXI_ADDR_W-5:0] waddr_q;
    logic [31:0] wdata_q;
    logic la_d, lb_d, lm_d;

    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            awready_r   <= 1'b1;
            wready_r    <= 1'b1;
            bvalid_r    <= 1'b0;
            waddr_q     <= '0;
            wdata_q     <= '0;
            sec_arm_reg <= '0;
            cfg_valid   <= 1'b0;
            cfg_disarm  <= 1'b0;
            pps_count   <= '0;
            la_d        <= 1'b0;
            lb_d        <= 1'b0;
            lm_d        <= 1'b0;
            lost_lock   <= 1'b0;
        end else begin
            cfg_valid <= 1'b0;

            if (pps_edge_mon) pps_count <= pps_count + 1'b1;

            // lost_lock sticky: any copy dropping out of lock (a disarm also
            // sets it; the PS clears the sticky after a deliberate re-arm).
            la_d <= locked_a_s;
            lb_d <= locked_b_s;
            lm_d <= locked_mon;
            if ((la_d && !locked_a_s) || (lb_d && !locked_b_s) ||
                (lm_d && !locked_mon))
                lost_lock <= 1'b1;

            if (s_axi_awvalid && awready_r) begin
                awready_r <= 1'b0;
                waddr_q   <= s_axi_awaddr[AXI_ADDR_W-1:4];
            end
            if (s_axi_wvalid && wready_r) begin
                wready_r <= 1'b0;
                wdata_q  <= s_axi_wdata;
            end
            if (!awready_r && !wready_r && !bvalid_r) begin
                bvalid_r <= 1'b1;
                if (waddr_q == 'd1) begin                 // SEC_ARM @ 0x10
                    sec_arm_reg <= wdata_q;
                    cfg_valid   <= 1'b1;
                    cfg_disarm  <= 1'b0;
                end
                if (waddr_q == 'd6) begin                 // CTRL @ 0x60
                    if (wdata_q[0]) lost_lock <= 1'b0;
                    if (wdata_q[1]) begin
                        cfg_valid  <= 1'b1;
                        cfg_disarm <= 1'b1;
                    end
                end
            end
            if (bvalid_r && s_axi_bready) begin
                bvalid_r  <= 1'b0;
                awready_r <= 1'b1;
                wready_r  <= 1'b1;
            end
        end
    end

    assign s_axi_awready = awready_r;
    assign s_axi_wready  = wready_r;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;            // OKAY
    assign cfg_sec       = sec_arm_reg;

endmodule
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `.\sim.ps1 run -Module wr_timebase_axi`
Expected: PASS (2 tests), plot at `sim_build/wr_timebase_axi/plots/now_reads.png`.

- [ ] **Step 5: Commit**

```bash
git add rtl/wr_timebase_axi.sv tb/wr_timebase_axi/
git commit -m "feat(rtl): wr_timebase_axi S_AXI3 register slave (arm/status/diagnostics + monitor instance)"
```

---

### Task 4: Pipeline chain integration sim

**Files:**
- Modify: `tb/aclk_pipeline_chain/tb_aclk_pipeline_chain_top.sv`
- Modify: `tb/aclk_pipeline_chain/test_aclk_pipeline_chain.py`
- Modify: `tb/aclk_pipeline_chain/runner.py`

**Interfaces:**
- Consumes: `wr_timebase`, `wr_timebase_axi`, `cdc_word_pulse` (Tasks 1-3), `WrGen` (Task 2), `axi_lite_bfm` with `pfx="s3_"` for the new slave.
- Produces: the executable proof that both readouts stamp one WR timeline; Task 5 copies this exact wiring into the hardware top.

- [ ] **Step 1: Update the testbench top (write the failing test first: the tb references the new wiring before the test file changes run against it)**

In `tb/aclk_pipeline_chain/tb_aclk_pipeline_chain_top.sv`:

(a) Update the header comment block: replace the `global_timebase provides a shared 64-bit tick counter: ...` paragraph (lines 13-17) with:

```systemverilog
// Two wr_timebase replicas + the wr_timebase_axi monitor/register slave
// (s3_s_axi_*) replace global_timebase: both readouts stamp events with the
// same WR-disciplined {sec, ns} timeline, strictly zero until armed + locked.
// Sim second = 50 WR cells = 5 us (watchdog params scaled to match).
```

(b) Add ports after `input wire tclk,`:

```systemverilog
    // White Rabbit reference inputs (async; shared by all timebase copies)
    input  wire wr_clk10,
    input  wire wr_pps,
```

(c) Add a third AXI port set at the end of the port list (after the `s2_s_axi_rready` line, adding a comma to it):

```systemverilog
    // ---- AXI4-Lite slave #3: wr_timebase_axi (pfx="s3_" -> s3_s_axi_*) ----
    input  wire        s3_s_axi_aclk,
    input  wire        s3_s_axi_aresetn,
    input  wire [7:0]  s3_s_axi_awaddr,
    input  wire        s3_s_axi_awvalid,
    output wire        s3_s_axi_awready,
    input  wire [31:0] s3_s_axi_wdata,
    input  wire [3:0]  s3_s_axi_wstrb,
    input  wire        s3_s_axi_wvalid,
    output wire        s3_s_axi_wready,
    output wire [1:0]  s3_s_axi_bresp,
    output wire        s3_s_axi_bvalid,
    input  wire        s3_s_axi_bready,
    input  wire [7:0]  s3_s_axi_araddr,
    input  wire        s3_s_axi_arvalid,
    output wire        s3_s_axi_arready,
    output wire [31:0] s3_s_axi_rdata,
    output wire [1:0]  s3_s_axi_rresp,
    output wire        s3_s_axi_rvalid,
    input  wire        s3_s_axi_rready
```

(d) Replace the whole `global_timebase` instantiation block (the `u_tb` instance and the `ts_tclk`/`ts_aclk` wire comments) with:

```systemverilog
    // ----------------------------------------------------------------
    // WR timebase: one replica per event domain + the AXI monitor slave.
    // Sim-scaled watchdogs for the 50-cell (5 us) sim second.
    // ----------------------------------------------------------------
    wire        cfg_valid, cfg_disarm;
    wire [31:0] cfg_sec;
    wire [63:0] ts_tclk;   // clk_40m domain -> readout#1
    wire [63:0] ts_aclk;   // clk_tx domain -> readout#2
    wire        tb_locked_tclk, tb_locked_aclk;

    wr_timebase #(
        .CLK_PERIOD_DS (250),
        .CLK10_TIMEOUT (16),     // 400 ns at 40 MHz
        .PPS_TIMEOUT   (240)     // 6 us at 40 MHz
    ) u_tb_tclk (
        .clk(clk_40m), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(s_axi_aclk), .cfg_rstn(s_axi_aresetn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_tclk), .locked(tb_locked_tclk), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

    wr_timebase #(
        .CLK_PERIOD_DS (160),
        .CLK10_TIMEOUT (25),     // 400 ns at 62.5 MHz
        .PPS_TIMEOUT   (375)     // 6 us at 62.5 MHz
    ) u_tb_aclk (
        .clk(clk_tx), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(s_axi_aclk), .cfg_rstn(s_axi_aresetn),
        .cfg_valid(cfg_valid), .cfg_disarm(cfg_disarm), .cfg_sec(cfg_sec),
        .ts(ts_aclk), .locked(tb_locked_aclk), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

    wr_timebase_axi #(
        .AXI_ADDR_W        (8),
        .MON_CLK10_TIMEOUT (40),    // 400 ns at 100 MHz
        .MON_PPS_TIMEOUT   (600)    // 6 us at 100 MHz
    ) u_tb_axi (
        .wr_clk10   (wr_clk10),
        .wr_pps     (wr_pps),
        .locked_a   (tb_locked_tclk),
        .locked_b   (tb_locked_aclk),
        .cfg_valid  (cfg_valid),
        .cfg_disarm (cfg_disarm),
        .cfg_sec    (cfg_sec),
        .s_axi_aclk    (s3_s_axi_aclk),
        .s_axi_aresetn (s3_s_axi_aresetn),
        .s_axi_awaddr  (s3_s_axi_awaddr),
        .s_axi_awvalid (s3_s_axi_awvalid),
        .s_axi_awready (s3_s_axi_awready),
        .s_axi_wdata   (s3_s_axi_wdata),
        .s_axi_wstrb   (s3_s_axi_wstrb),
        .s_axi_wvalid  (s3_s_axi_wvalid),
        .s_axi_wready  (s3_s_axi_wready),
        .s_axi_bresp   (s3_s_axi_bresp),
        .s_axi_bvalid  (s3_s_axi_bvalid),
        .s_axi_bready  (s3_s_axi_bready),
        .s_axi_araddr  (s3_s_axi_araddr),
        .s_axi_arvalid (s3_s_axi_arvalid),
        .s_axi_arready (s3_s_axi_arready),
        .s_axi_rdata   (s3_s_axi_rdata),
        .s_axi_rresp   (s3_s_axi_rresp),
        .s_axi_rvalid  (s3_s_axi_rvalid),
        .s_axi_rready  (s3_s_axi_rready)
    );
```

- [ ] **Step 2: Update the runner sources**

In `tb/aclk_pipeline_chain/runner.py`, replace the line

```python
            # Shared timebase
            "rtl/global_timebase.v",
```

with

```python
            # WR timebase (shared timeline, per-domain replicas + AXI monitor)
            "rtl/cdc_word_pulse.sv",
            "rtl/wr_timebase.sv",
            "rtl/wr_timebase_axi.sv",
```

- [ ] **Step 3: Update the test**

Replace the full contents of `tb/aclk_pipeline_chain/test_aclk_pipeline_chain.py` with:

```python
"""Full pure-RTL chain with the WR timebase: a TCLK biphase stimulus must appear
at BOTH readouts stamped on the shared WR {sec, ns} timeline.

Phases:
  1. UNSYNCED: an event driven before arming carries ts == 0 at both readouts.
  2. Arm over the s3 slave, wait for all three lock bits.
  3. Drive 3 events: both readouts decode them, timestamps are WR sec:ns,
     the matched pairs agree (ACLK stamped later, within a bounded delta).

Sim second = 50 WR cells = 5000 ns (tb/wr_model.py)."""

import warnings
import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import ClockCycles, Timer

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from axi_lite_bfm import axi_read, axi_write   # noqa: E402
from tclk_tx_model import stream_samples, drive_samples  # noqa: E402
from wr_model import WrGen  # noqa: E402

# Readout register byte offsets (same for both readouts)
STATUS      = 0x00
EVENT       = 0x10
DATA_HI     = 0x20
DATA_LO     = 0x30
TS_HI       = 0x40
TS_LO       = 0x50
POP         = 0x60
EVENT_COUNT = 0x70

# wr_timebase_axi register byte offsets (s3_ slave)
WR_STATUS, WR_SEC_ARM, WR_SEC_NOW, WR_NS_NOW = 0x00, 0x10, 0x20, 0x30

PRE_EVENT = [0x05]           # driven BEFORE arming: must stamp ts == 0
EVENTS    = [0x02, 0x07, 0x42]

SIM_NS_PER_SEC = 5000        # 50 cells * 100 ns
SEC0 = 1_751_800_000

WARMUP_CELLS = 200
GAP_CELLS    = 800


def _split(ts):
    return (ts >> 32) & 0xFFFFFFFF, ts & 0xFFFFFFFF


def _combined(ts):
    sec, ns = _split(ts)
    return (sec - SEC0) * SIM_NS_PER_SEC + ns


async def pop_all(dut, pfx):
    """Drain all events from the FIFO selected by `pfx`. Returns list of (ev, ts)."""
    out = []
    while True:
        status = await axi_read(dut, STATUS, pfx=pfx)
        if status & 0x1:
            break
        ev = await axi_read(dut, EVENT, pfx=pfx)
        ts = ((await axi_read(dut, TS_HI, pfx=pfx)) << 32) | (await axi_read(dut, TS_LO, pfx=pfx))
        await axi_read(dut, DATA_HI, pfx=pfx)
        await axi_read(dut, DATA_LO, pfx=pfx)
        await axi_write(dut, POP, 0, pfx=pfx)
        out.append((ev & 0xFFFF, ts))
    return out


async def drive_events(dut, events):
    samples = stream_samples(events, warmup_cells=WARMUP_CELLS, gap_cells=GAP_CELLS)
    await drive_samples(dut.clk_80m, dut.tclk, samples)
    # settle: last frame through encoder, ACLK_RCV, and both CDC FIFOs
    await ClockCycles(dut.clk_80m, 5000)
    await ClockCycles(dut.s_axi_aclk, 20)


@cocotb.test()
async def test_full_chain_wr(dut):
    # Start all clocks (s_axi/s2/s3 aclk all 100 MHz, same as pl_clk0 on HW)
    cocotb.start_soon(Clock(dut.clk_80m,       12.5, unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_40m,       25,   unit="ns").start())
    cocotb.start_soon(Clock(dut.clk_tx,        16,   unit="ns").start())
    cocotb.start_soon(Clock(dut.pl_clk0,       10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk,    10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s2_s_axi_aclk, 10,   unit="ns").start())
    cocotb.start_soon(Clock(dut.s3_s_axi_aclk, 10,   unit="ns").start())

    dut.rstn.value              = 0
    dut.s_axi_aresetn.value     = 0
    dut.s2_s_axi_aresetn.value  = 0
    dut.s3_s_axi_aresetn.value  = 0
    dut.tclk.value              = 1
    dut.wr_clk10.value          = 0
    dut.wr_pps.value            = 0

    for sig in ("awaddr", "awvalid", "wdata", "wstrb", "wvalid",
                "bready", "araddr", "arvalid", "rready"):
        getattr(dut, "s_axi_"    + sig).value = 0
        getattr(dut, "s2_s_axi_" + sig).value = 0
        getattr(dut, "s3_s_axi_" + sig).value = 0

    await ClockCycles(dut.pl_clk0, 12)
    await Timer(1, unit="ns")
    dut.rstn.value              = 1
    dut.s_axi_aresetn.value     = 1
    dut.s2_s_axi_aresetn.value  = 1
    dut.s3_s_axi_aresetn.value  = 1

    gen = WrGen(dut.wr_clk10, dut.wr_pps)
    gen.start()

    # encoder RAM-zeroing sweep + ACLK_RCV alignment
    await ClockCycles(dut.clk_tx, 350)

    # ---- phase 1: UNSYNCED event carries ts == 0 at both readouts ----
    await drive_events(dut, PRE_EVENT)
    pre_tclk = await pop_all(dut, "")
    pre_aclk = await pop_all(dut, "s2_")
    assert [e & 0xFF for (e, _) in pre_tclk] == PRE_EVENT
    assert [e & 0xFF for (e, _) in pre_aclk] == PRE_EVENT
    assert pre_tclk[0][1] == 0, f"unsynced TCLK ts {pre_tclk[0][1]:#x} != 0"
    assert pre_aclk[0][1] == 0, f"unsynced ACLK ts {pre_aclk[0][1]:#x} != 0"

    # ---- phase 2: arm over the s3 slave, wait for all three lock bits ----
    await axi_write(dut, WR_SEC_ARM, SEC0, pfx="s3_")
    for _ in range(600):
        s = await axi_read(dut, WR_STATUS, pfx="s3_")
        if (s & 0x7) == 0x7:
            break
        await ClockCycles(dut.s3_s_axi_aclk, 20)
    else:
        raise AssertionError(f"timebase never fully locked: STATUS=0x{s:08X}")

    # ---- phase 3: events land on the shared WR timeline ----
    await drive_events(dut, EVENTS)
    tclk_events = await pop_all(dut, "")
    aclk_events = await pop_all(dut, "s2_")
    dut._log.info(f"TCLK readout events: {tclk_events}")
    dut._log.info(f"ACLK readout events: {aclk_events}")

    assert [e & 0xFF for (e, _) in tclk_events] == EVENTS
    assert [e & 0xFF for (e, _) in aclk_events] == EVENTS

    for (ev_t, ts_t), (ev_a, ts_a) in zip(tclk_events, aclk_events):
        sec_t, ns_t = _split(ts_t)
        sec_a, ns_a = _split(ts_a)
        assert sec_t >= SEC0, f"TCLK sec {sec_t} below armed label"
        assert sec_a >= SEC0, f"ACLK sec {sec_a} below armed label"
        assert ns_t < SIM_NS_PER_SEC and ns_a < SIM_NS_PER_SEC
        ct, ca = _combined(ts_t), _combined(ts_a)
        # ACLK is stamped after the encoder + ACLK_RCV traversal: later than
        # TCLK (allow 150 ns of cross-domain sync skew), within a bounded delta.
        assert ca >= ct - 150, f"ACLK ts precedes TCLK ts: {ca} < {ct}"
        assert ca - ct <= 20_000, f"ACLK ts too far after TCLK ts: {ca - ct} ns"

    _save_chain_plot(tclk_events, aclk_events, len(EVENTS))
    gen.stop()


def _save_chain_plot(tclk_events, aclk_events, n_events):
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception as exc:                        # noqa: BLE001
        warnings.warn(f"matplotlib unavailable, skipping plot: {exc}")
        return
    fig, ax = plt.subplots(figsize=(9, 5))
    xs = list(range(1, len(tclk_events) + 1))
    ax.step(xs, [_combined(ts) for (_, ts) in tclk_events],
            where="post", color="tab:blue", lw=1.8, label="TCLK readout (WR ns since arm)")
    ax.step(list(range(1, len(aclk_events) + 1)),
            [_combined(ts) for (_, ts) in aclk_events],
            where="post", color="tab:orange", lw=1.8, label="ACLK readout (WR ns since arm)")
    ax.set_xlabel("event index")
    ax.set_ylabel("WR timeline (sim ns since armed second)")
    ax.set_title(f"Pipeline chain: {n_events} events on the shared WR timeline")
    ax.legend(loc="upper left")
    ax.grid(True, alpha=0.3)
    out_dir = (Path(__file__).resolve().parents[2]
               / "sim_build" / "aclk_pipeline_chain" / "plots")
    out_dir.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(out_dir / "pipeline_chain_timestamps.png", dpi=120)
    plt.close(fig)
```

- [ ] **Step 4: Run the chain suite**

Run: `.\sim.ps1 run -Module aclk_pipeline_chain`
Expected: PASS (1 test), plot regenerated. If phase 1 fails with a nonzero ts, the strict gating is broken; if phase 3 deltas exceed 20 us, check GAP_CELLS reached both FIFOs before draining.

- [ ] **Step 5: Confirm the pre-existing timebase suite still passes (global_timebase stays in the repo)**

Run: `.\sim.ps1 run -Module global_timebase`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tb/aclk_pipeline_chain/
git commit -m "test(chain): pipeline chain sim on the WR timebase (unsynced ts==0, arm via S_AXI3, shared sec:ns timeline)"
```

---

### Task 5: Hardware top, constraints, build script, overlay

**Files:**
- Modify: `rtl/aclk_pipeline_bd_top.v`
- Modify: `constraints/kr260_aclk_pipeline.xdc`
- Modify: `vivado/build_aclk_pipeline.tcl`
- Modify: `deploy/aclk_pipeline.dts`

**Interfaces:**
- Consumes: `wr_timebase`, `wr_timebase_axi` exactly as instantiated in the Task 4 tb (production watchdog values swapped in).
- Produces: BD top port names `wr_clk10`, `wr_pps`, `s_axi3_*` (interface `S_AXI3` at 0x8002_0000); overlay node `wr_timebase@80020000`. Task 6's tooling talks to that UIO node.

There is no simulation for the BD top (it instantiates the GT IP); the Task 4 chain sim proves the identical wiring. Verification here is careful review plus the Vivado build gate at the end.

- [ ] **Step 1: Add the WR ports to `rtl/aclk_pipeline_bd_top.v`**

After the `input wire tclk,` port (line 36), insert:

```verilog
    // ---- White Rabbit reference inputs (Pmod 1: pin3 = E10 10 MHz, pin4 = E12 PPS) ----
    input  wire        wr_clk10,
    input  wire        wr_pps,
```

- [ ] **Step 2: Extend the shared-AXI clock association and add the S_AXI3 port set**

Change the `ASSOCIATED_BUSIF` attribute on `s_axi_aclk` (line 72) from `"ASSOCIATED_BUSIF S_AXI:S_AXI2, ..."` to:

```verilog
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:S_AXI2:S_AXI3, ASSOCIATED_RESET s_axi_aresetn" *)
```

After the S_AXI2 port block (after `input wire s_axi2_rready`, adding a trailing comma to it), add:

```verilog
    // ==== AXI4-Lite slave #3: WR timebase (bus S_AXI3) ====
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 AWADDR" *)
    input  wire [7:0]  s_axi3_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 AWVALID" *)
    input  wire        s_axi3_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 AWREADY" *)
    output wire        s_axi3_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 WDATA" *)
    input  wire [31:0] s_axi3_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 WSTRB" *)
    input  wire [3:0]  s_axi3_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 WVALID" *)
    input  wire        s_axi3_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 WREADY" *)
    output wire        s_axi3_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 BRESP" *)
    output wire [1:0]  s_axi3_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 BVALID" *)
    output wire        s_axi3_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 BREADY" *)
    input  wire        s_axi3_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 ARADDR" *)
    input  wire [7:0]  s_axi3_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 ARVALID" *)
    input  wire        s_axi3_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 ARREADY" *)
    output wire        s_axi3_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 RDATA" *)
    output wire [31:0] s_axi3_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 RRESP" *)
    output wire [1:0]  s_axi3_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 RVALID" *)
    output wire        s_axi3_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI3 RREADY" *)
    input  wire        s_axi3_rready
```

- [ ] **Step 3: Replace the shared-timebase section**

Replace the whole `global_timebase` section (the `Shared 64-bit timebase` banner comment, the `ts_tclk`/`ts_aclk` wires, and the `u_tb` instance, lines 351-363) with:

```verilog
    // =====================================================================
    // White Rabbit timebase: one wr_timebase replica per event domain plus
    // the wr_timebase_axi monitor/register slave (S_AXI3). All copies watch
    // the same two pins, so both readouts stamp one {sec, ns} timeline.
    // STRICT: ts reads 0 until the PS arms seconds (deploy/wr_time.py) and a
    // PPS loads them; loss of either reference unlocks and requires a re-arm.
    // NOTE: a GT relock stops rx_usrclk2 and resets the ACLK replica, so it
    // unlocks; re-arm after any GT recovery (the runbook covers this).
    // =====================================================================
    wire        wr_cfg_valid, wr_cfg_disarm;
    wire [31:0] wr_cfg_sec;
    wire [63:0] ts_tclk;   // clk_40m domain (TCLK readout)
    wire [63:0] ts_aclk;   // rx_usrclk2 domain (ACLK readout)
    wire        tb_locked_tclk, tb_locked_aclk;

    wr_timebase #(
        .CLK_PERIOD_DS (250),          // clk_40m: 25.0 ns
        .CLK10_TIMEOUT (16),           // 400 ns at 40 MHz
        .PPS_TIMEOUT   (44_000_000)    // 1.1 s at 40 MHz
    ) u_tb_tclk (
        .clk(clk_40m), .rstn(rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(s_axi_aclk), .cfg_rstn(s_axi_aresetn),
        .cfg_valid(wr_cfg_valid), .cfg_disarm(wr_cfg_disarm), .cfg_sec(wr_cfg_sec),
        .ts(ts_tclk), .locked(tb_locked_tclk), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

    wr_timebase #(
        .CLK_PERIOD_DS (160),          // rx_usrclk2: 16.0 ns (1.25 Gbps / 20 = 62.5 MHz)
        .CLK10_TIMEOUT (25),           // 400 ns at 62.5 MHz
        .PPS_TIMEOUT   (68_750_000)    // 1.1 s at 62.5 MHz
    ) u_tb_aclk (
        .clk(rx_usrclk2), .rstn(ro_rstn), .wr_clk10(wr_clk10), .wr_pps(wr_pps),
        .cfg_clk(s_axi_aclk), .cfg_rstn(s_axi_aresetn),
        .cfg_valid(wr_cfg_valid), .cfg_disarm(wr_cfg_disarm), .cfg_sec(wr_cfg_sec),
        .ts(ts_aclk), .locked(tb_locked_aclk), .arm_pending(),
        .pps_alive(), .clk10_alive(), .pps_edge(), .cells_last()
    );

    wr_timebase_axi #(
        .AXI_ADDR_W (8)                // production monitor defaults (100 MHz)
    ) u_tb_axi (
        .wr_clk10   (wr_clk10),
        .wr_pps     (wr_pps),
        .locked_a   (tb_locked_tclk),
        .locked_b   (tb_locked_aclk),
        .cfg_valid  (wr_cfg_valid),
        .cfg_disarm (wr_cfg_disarm),
        .cfg_sec    (wr_cfg_sec),
        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi3_awaddr),
        .s_axi_awvalid (s_axi3_awvalid),
        .s_axi_awready (s_axi3_awready),
        .s_axi_wdata   (s_axi3_wdata),
        .s_axi_wstrb   (s_axi3_wstrb),
        .s_axi_wvalid  (s_axi3_wvalid),
        .s_axi_wready  (s_axi3_wready),
        .s_axi_bresp   (s_axi3_bresp),
        .s_axi_bvalid  (s_axi3_bvalid),
        .s_axi_bready  (s_axi3_bready),
        .s_axi_araddr  (s_axi3_araddr),
        .s_axi_arvalid (s_axi3_arvalid),
        .s_axi_arready (s_axi3_arready),
        .s_axi_rdata   (s_axi3_rdata),
        .s_axi_rresp   (s_axi3_rresp),
        .s_axi_rvalid  (s_axi3_rvalid),
        .s_axi_rready  (s_axi3_rready)
    );
```

Also update the top-of-file header comment: replace the `Shared timebase: global_timebase runs in s_axi_aclk (pl_clk0) ...` paragraph (lines 28-30) with:

```verilog
// Shared timebase: White Rabbit disciplined. wr_timebase replicas in clk_40m and
// rx_usrclk2 (plus a monitor in s_axi_aclk behind S_AXI3) all watch the same two
// Pmod pins (wr_clk10 10 MHz, wr_pps PPS); both readouts stamp {sec, ns} on one
// timeline (USE_EXT_TS=1). ts is STRICTLY 0 until armed + WR-locked.
```

- [ ] **Step 4: Add the pin constraints**

In `constraints/kr260_aclk_pipeline.xdc`, after the `dbg_hb` LOC (line 44), add:

```tcl
## White Rabbit reference inputs: PMOD1 pin 3 = package E10 (10 MHz), pin 4 = E12 (PPS).
## Both are ASYNC DATA inputs (2-FF synced per consuming domain, like tclk): ordinary
## LVCMOS33 pins, no clock-capable routing, no create_clock, no set_input_delay.
set_property -dict {PACKAGE_PIN E10 IOSTANDARD LVCMOS33} [get_ports wr_clk10]
set_property -dict {PACKAGE_PIN E12 IOSTANDARD LVCMOS33} [get_ports wr_pps]
```

Also update the async-clock-groups comment block: in the `Which CDC each cross-group cut covers` list, replace the two mentions of `global_timebase gray CDC` with `the wr_timebase cfg toggle-CDC (cdc_word_pulse)` (the A<->B and A<->C lines); the group structure itself does not change.

- [ ] **Step 5: Update `vivado/build_aclk_pipeline.tcl`**

(a) In the `Common readout + CDC primitives` add_files list, add after the `cdc_gray_count.sv` line:

```tcl
    [file join $rtl_dir cdc_word_pulse.sv] \
    [file join $rtl_dir wr_timebase.sv] \
    [file join $rtl_dir wr_timebase_axi.sv] \
```

(b) In the `Pipeline glue` add_files list, delete the line:

```tcl
    [file join $rtl_dir global_timebase.v] \
```

(c) SmartConnect: change `CONFIG.NUM_MI {2}` to `CONFIG.NUM_MI {3}` and add after the `M01_AXI` connection:

```tcl
connect_bd_intf_net [get_bd_intf_pins axi_sc/M02_AXI] [get_bd_intf_pins u_pipeline/S_AXI3]
```

(d) External ports: after the `dbg_hb` port connections, add:

```tcl
# White Rabbit reference inputs (Pmod 1 pins 3/4 = E10/E12).
create_bd_port -dir I wr_clk10
create_bd_port -dir I wr_pps
connect_bd_net [get_bd_port wr_clk10] [get_bd_pins u_pipeline/wr_clk10]
connect_bd_net [get_bd_port wr_pps]   [get_bd_pins u_pipeline/wr_pps]
```

(e) Address map: after the S_AXI2 assign_bd_address block, add:

```tcl
assign_bd_address -offset 0x80020000 -range 0x10000 -force -target_address_space \
    [get_bd_addr_spaces zynq_ultra_ps_e_0/Data] \
    [get_bd_addr_segs -of_objects [get_bd_intf_pins u_pipeline/S_AXI3]]
```

Also update the header comment `Address map: the two AXI4-Lite slaves ...` to say `three AXI4-Lite slaves at 0x8000_0000 / 0x8001_0000 / 0x8002_0000` and the SmartConnect comment `NUM_MI=2` to `NUM_MI=3`.

- [ ] **Step 6: Add the third UIO node to `deploy/aclk_pipeline.dts`**

In fragment@1, after the `aclk_readout_axi` node, add:

```dts
            wr_timebase_axi: wr_timebase@80020000 {
                compatible = "generic-uio";
                reg = <0x0 0x80020000 0x0 0x10000>;
            };
```

And extend the header comment's node list with:
`*   - wr_timebase  @ 0x8002_0000  (WR timebase: arm seconds, lock status)` and a readers line `* WR time:  sudo python3 wr_time.py /dev/uioK status` after the existing Readers lines.

- [ ] **Step 7: Verify nothing regressed in sim**

Run: `.\sim.ps1 run -Module aclk_pipeline_chain`
Expected: PASS (the tb tops are separate files, but this catches accidental edits to shared RTL).

- [ ] **Step 8: Commit**

```bash
git add rtl/aclk_pipeline_bd_top.v constraints/kr260_aclk_pipeline.xdc vivado/build_aclk_pipeline.tcl deploy/aclk_pipeline.dts
git commit -m "feat(pipeline): WR timebase in the BD top (E10/E12 inputs, S_AXI3 slave at 0x80020000, overlay node)"
```

- [ ] **Step 9: (Gate, long, run when ready for hardware) Vivado build**

Run: `.\hw.ps1 build -Tcl vivado\build_aclk_pipeline.tcl -Name aclk_pipeline`
Expected: `BITSTREAM: ...uart_echo_bd_wrapper.bit` banner; no NSTD-1/UCIO-1 DRC (every new port has a LOC + IOSTANDARD). This step is the only verification Vivado-side changes get; do not claim hardware readiness before it passes.

---

### Task 6: PS tooling (arm helper, reader decode, runbook)

**Files:**
- Modify: `deploy/readout_common.py`
- Modify: `deploy/test_readout_common.py`
- Create: `deploy/wr_time.py`
- Create: `deploy/test_wr_time.py`
- Modify: `deploy/tclk_read.py`
- Modify: `deploy/aclkgt_read.py`
- Create: `deploy/wr.md`

**Interfaces:**
- Consumes: `readout_common.RegIO` / `open_dev` / `parse_args` / `stream_events`; the Task 3 register map (offsets 0x00-0x60 on the wr_timebase UIO node).
- Produces: `readout_common.wr_split(ts) -> (sec, ns)`, `readout_common.wr_utc(ts) -> str` ("UNSYNC" for 0), `stream_events(..., wr=False)`; `wr_time.py` commands `status` and `arm`; `--wr` flag on both pipeline readers.

- [ ] **Step 1: Write the failing tests**

Append to `deploy/test_readout_common.py` (before the `if __name__` block):

```python
def test_wr_split_and_utc():
    from readout_common import wr_split, wr_utc
    ts = (1_751_800_000 << 32) | 123_456_789
    assert wr_split(ts) == (1_751_800_000, 123_456_789)
    assert wr_utc(0) == "UNSYNC"                    # strict-zero timestamp
    s = wr_utc((0 << 32) | 123)                     # epoch second 0
    assert s == "1970-01-01T00:00:00.000000123Z"
    s = wr_utc(ts)
    assert s.endswith(".123456789Z") and s.startswith("20")
```

Create `deploy/test_wr_time.py`:

```python
"""Unit tests for wr_time helpers (no hardware).
Run: python deploy/test_wr_time.py   or   pytest deploy -q"""
from wr_time import next_pps_label, decode_status, ARM_FRAC_LO, ARM_FRAC_HI


def test_next_pps_label():
    assert next_pps_label(1_751_800_000.5) == 1_751_800_001
    assert next_pps_label(1_751_800_000.11) == 1_751_800_001


def test_arm_window_constants_leave_margin():
    assert 0.05 <= ARM_FRAC_LO < ARM_FRAC_HI <= 0.95


def test_decode_status():
    d = decode_status(0x0000_0107)
    assert d["locked_tclk"] and d["locked_aclk"] and d["locked_mon"]
    assert not d["pps_alive"] and not d["clk10_alive"] and not d["arm_pending"]
    assert d["lost_lock"]
    d = decode_status(0x0000_0038)
    assert d["pps_alive"] and d["clk10_alive"] and d["arm_pending"]
    assert not d["locked_tclk"] and not d["lost_lock"]


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print("ok:", name)
    print("all wr_time tests passed")
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& .venv\Scripts\python.exe deploy\test_readout_common.py`
Expected: FAIL (`ImportError: cannot import name 'wr_split'`).
Run: `& .venv\Scripts\python.exe deploy\test_wr_time.py`
Expected: FAIL (`ModuleNotFoundError: No module named 'wr_time'`).

- [ ] **Step 3: Implement the readout_common helpers**

In `deploy/readout_common.py`:

(a) Append after `def dev_offset(dev): ...`:

```python
def wr_split(ts):
    """Split a packed White Rabbit timestamp into (sec, ns)."""
    return (ts >> 32) & 0xFFFFFFFF, ts & 0xFFFFFFFF


def wr_utc(ts):
    """Human-readable UTC for a packed WR timestamp. The strict-zero value
    (stamped while not WR-locked) renders as 'UNSYNC'."""
    if ts == 0:
        return "UNSYNC"
    sec, ns = wr_split(ts)
    base = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(sec))
    return "%s.%09dZ" % (base, ns)
```

(b) Change the `stream_events` signature and dt computation:

```python
def stream_events(io, tick_ns, stats_line, format_event, header, wr=False):
    """The shared drain loop: poll STATUS, print each event via format_event, emit a
    stats line every ~1 s while idle. Runs until Ctrl-C, then prints a final stats
    line. format_event(ts, dt, event, data, is_tclk, has_data) -> str.
    wr=True: ts is a WR {sec, ns} pair; dt_us comes from real nanoseconds and is
    suppressed around UNSYNC (zero) stamps. wr=False: legacy tick behavior."""
    say(header)
    last_ts = None
    last_stats = time.monotonic()
    try:
        while True:
            if io.rd(STATUS) & 0x1:                    # empty
                now = time.monotonic()
                if now - last_stats >= 1.0:
                    say(stats_line())
                    last_stats = now
                time.sleep(0.001)
                continue
            event, flags, data, ts = read_event(io)
            is_tclk = (flags >> 1) & 1
            has_data = flags & 1
            if wr:
                if last_ts in (None, 0) or ts == 0:
                    dt = "   --  "
                else:
                    s2, n2 = wr_split(ts)
                    s1, n1 = wr_split(last_ts)
                    dt = "%7.1f" % (((s2 - s1) * 1_000_000_000 + (n2 - n1)) / 1000.0)
            else:
                dt = "   --  " if last_ts is None else "%7.1f" % ((ts - last_ts) * tick_ns / 1000.0)
            last_ts = ts
            say(format_event(ts, dt, event, data, is_tclk, has_data))
    except KeyboardInterrupt:
        say("\n# stopped.")
        say(stats_line())
```

- [ ] **Step 4: Create `deploy/wr_time.py`**

```python
#!/usr/bin/env python3
"""Arm and monitor the White Rabbit timebase over UIO (wr_timebase_axi @ 0x8002_0000).

    sudo python3 wr_time.py /dev/uio6 status     # lock state + HW-vs-system delta
    sudo python3 wr_time.py /dev/uio6 arm        # arm the next-PPS Unix label from NTP time
    sudo python3 wr_time.py /dev/uio6 disarm     # force unlock (CTRL[1])
    sudo python3 wr_time.py /dev/uio6 clear      # clear the lost_lock sticky (CTRL[0])

Arm protocol: the PS system clock is NTP-disciplined; mid-second (to avoid racing
the PPS boundary) we write floor(now)+1, the label of the NEXT PPS, to SEC_ARM.
Hardware loads it at that PPS in every timebase copy and locks. Verify compares
SEC_NOW against the system clock afterwards. STRICT: any reference loss (or a GT
relock, which resets the ACLK replica) unlocks and needs a fresh `arm`.
"""
import sys
import time

import readout_common as rc
from readout_common import say

# wr_timebase_axi register map (16-byte stride)
WR_STATUS, WR_SEC_ARM, WR_SEC_NOW, WR_NS_NOW, WR_PPS_COUNT, WR_CELLS_LAST, WR_CTRL = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60)
WR_NAME = {WR_STATUS: "STATUS", WR_SEC_ARM: "SEC_ARM", WR_SEC_NOW: "SEC_NOW",
           WR_NS_NOW: "NS_NOW", WR_PPS_COUNT: "PPS_COUNT",
           WR_CELLS_LAST: "CELLS_LAST", WR_CTRL: "CTRL"}

CELLS_EXPECTED = 10_000_000     # 10 MHz cells per real second
ARM_FRAC_LO, ARM_FRAC_HI = 0.10, 0.80   # safe window within the second for arming


def next_pps_label(t):
    """Unix UTC label of the PPS that follows system time t."""
    return int(t) + 1


def decode_status(v):
    return {
        "locked_tclk": bool(v & 0x001),
        "locked_aclk": bool(v & 0x002),
        "locked_mon":  bool(v & 0x004),
        "pps_alive":   bool(v & 0x008),
        "clk10_alive": bool(v & 0x010),
        "arm_pending": bool(v & 0x020),
        "lost_lock":   bool(v & 0x100),
    }


def read_now(io):
    """Atomic {sec, ns} pair: the SEC_NOW read latches NS_NOW in hardware."""
    sec = io.rd(WR_SEC_NOW)
    ns = io.rd(WR_NS_NOW)
    return sec, ns


def cmd_status(io):
    s = decode_status(io.rd(WR_STATUS))
    say("# STATUS: " + "  ".join("%s=%d" % (k, int(v)) for k, v in s.items()))
    say("# PPS_COUNT=%d  CELLS_LAST=%d (expect %d; far off => flaky 10 MHz or PPS line)"
        % (io.rd(WR_PPS_COUNT), io.rd(WR_CELLS_LAST), CELLS_EXPECTED))
    sec, ns = read_now(io)
    if sec == 0 and ns == 0:
        say("# HW time: UNSYNC (strict zero: not armed / not locked)")
    else:
        sys_t = time.time()
        delta = (sec + ns / 1e9) - sys_t
        say("# HW time: %s  (HW - system clock = %+0.6f s)"
            % (rc.wr_utc((sec << 32) | ns), delta))
        if abs(delta) > 0.5:
            say("# !! WARNING: HW seconds disagree with the system clock; re-run 'arm'.")
    return s


def cmd_arm(io):
    # wait for a mid-second moment so the SEC_ARM write cannot race the PPS
    while True:
        t = time.time()
        frac = t % 1.0
        if ARM_FRAC_LO <= frac <= ARM_FRAC_HI:
            break
        time.sleep(0.02)
    label = next_pps_label(t)
    io.wr(WR_SEC_ARM, label)
    rb = io.rd(WR_SEC_ARM)
    if rb != label:
        say("# !! SEC_ARM readback 0x%08X != written 0x%08X; AXI write failed." % (rb, label))
        return
    say("# armed %d (UTC label of the next PPS); waiting for lock ..." % label)
    deadline = time.monotonic() + 2.5
    while time.monotonic() < deadline:
        s = decode_status(io.rd(WR_STATUS))
        if s["locked_mon"] and s["locked_tclk"] and s["locked_aclk"]:
            break
        time.sleep(0.05)
    cmd_status(io)


def main(argv):
    rc.line_buffer_stdout()
    pos, _flags = rc.parse_args(argv)
    dev = pos[0] if pos else "/dev/uio6"
    cmd = pos[1] if len(pos) > 1 else "status"
    io = rc.open_dev(dev)
    io.names = WR_NAME
    if cmd == "status":
        cmd_status(io)
    elif cmd == "arm":
        cmd_arm(io)
    elif cmd == "disarm":
        io.wr(WR_CTRL, 0x2)
        say("# disarmed (all copies unlock; timestamps read UNSYNC until re-armed).")
    elif cmd == "clear":
        io.wr(WR_CTRL, 0x1)
        say("# lost_lock sticky cleared.")
    else:
        say("usage: wr_time.py /dev/uioN [status|arm|disarm|clear]")


if __name__ == "__main__":
    main(sys.argv[1:])
```

- [ ] **Step 5: Run the unit tests**

Run: `& .venv\Scripts\python.exe deploy\test_readout_common.py`
Expected: `all readout_common tests passed` (including `ok: test_wr_split_and_utc`).
Run: `& .venv\Scripts\python.exe deploy\test_wr_time.py`
Expected: `all wr_time tests passed`.

- [ ] **Step 6: Add `--wr` to the two pipeline readers**

In `deploy/tclk_read.py`:
(a) change the parse line to include the flag:

```python
_pos, _flags = rc.parse_args(sys.argv[1:], value_flags=("--drop", "--tick-ns"),
                             bool_flags=("--wr",))
```

(b) after the `TICK_NS = ...` line add:

```python
WR = bool(_flags.get("--wr"))   # WR sec:ns timestamps (integrated pipeline bitstream)
```

(c) replace `format_event` with:

```python
def format_event(ts, dt, event, data, is_tclk, has_data):
    if WR:
        return "  %s %s   0x%02X    %d      %d" % (
            rc.wr_utc(ts).ljust(30), dt, event & 0xFF, is_tclk, has_data)
    return "  %16d %s   0x%02X    %d      %d" % (ts, dt, event & 0xFF, is_tclk, has_data)
```

(d) replace the final `rc.stream_events(...)` call with:

```python
rc.stream_events(io, TICK_NS, stats_line, format_event, wr=WR,
                 header=("#  utc                             dt_us   event  tclk  has_data"
                         if WR else
                         "#        ts_ticks    dt_us   event  tclk  has_data"))
```

In `deploy/aclkgt_read.py`, make the same three changes:
(a) add `"--wr"` to `bool_flags` (it already has `("--gtreset",)`, so: `bool_flags=("--gtreset", "--wr")`);
(b) add `WR = bool(_flags.get("--wr"))` after `TICK_NS = ...`;
(c) replace `format_event` with:

```python
def format_event(ts, dt, event, data, is_tclk, has_data):
    data_str = "0x%016X" % data if has_data else "       --         "
    if WR:
        return "  %s %s   0x%04X  %s    %d      %d" % (
            rc.wr_utc(ts).ljust(30), dt, event, data_str, is_tclk, has_data)
    return "  %16d %s   0x%04X  %s    %d      %d" % (ts, dt, event, data_str, is_tclk, has_data)
```

(d) replace the final `rc.stream_events(...)` call with:

```python
rc.stream_events(io, TICK_NS, stats_line, format_event, wr=WR,
                 header=("#  utc                             dt_us   event     data               tclk  has_data"
                         if WR else
                         "#        ts_ticks    dt_us   event     data               tclk  has_data"))
```

- [ ] **Step 7: Write the WR bring-up runbook**

Create `deploy/wr.md`:

```markdown
# White Rabbit timebase bring-up (integrated pipeline bitstream)

The pipeline stamps every TCLK/ACLK event with `{sec[31:0], ns[31:0]}` (Unix UTC :
nanoseconds), disciplined by a WR node's 10 MHz + PPS. STRICT semantics: a
timestamp of 0 means "not WR-synced when stamped"; readers print it as UNSYNC.

## Wiring (Pmod 1)

| Pmod 1 pin | package pin | signal |
|---|---|---|
| 1 | H12 | tclk (existing input) |
| 2 | B10 | aclk_lite_out (existing output) |
| 3 | E10 | wr_clk10 (WR 10 MHz, 3.3V CMOS in) |
| 4 | E12 | wr_pps (WR PPS, 3.3V CMOS in) |

The WR source must drive push-pull 3.3V CMOS (the carrier's auto-direction level
translators misbehave with open-drain). PPS must be phase-aligned to the 10 MHz
(a real WR node, or the replica generator project, does this by construction).

## Load

    dtc -@ -O dtb -o aclk_pipeline.dtbo aclk_pipeline.dts
    sudo xmutil unloadapp
    sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo

Three UIO nodes appear: tclk_readout @ 0x8000_0000, aclk_readout @ 0x8001_0000,
wr_timebase @ 0x8002_0000. Match /dev/uioN indices via
`grep . /sys/class/uio/uio*/name`.

## Sync (needs an NTP-disciplined system clock: chrony or systemd-timesyncd)

    sudo python3 wr_time.py /dev/uio6 status   # expect pps_alive=1 clk10_alive=1,
                                               # CELLS_LAST ~= 10000000
    sudo python3 wr_time.py /dev/uio6 arm      # arms floor(now)+1 for the next PPS
    sudo python3 wr_time.py /dev/uio6 status   # expect locked_* = 1, |HW-system| < 0.5 s

## Read events on the WR timeline

    sudo python3 tclk_read.py /dev/uio4 --wr
    sudo python3 aclkgt_read.py /dev/uio5 --wr

## Gotchas

- STRICT: pulling either WR line unlocks everything and sets the lost_lock
  sticky; timestamps read UNSYNC until you `arm` again. `clear` resets the sticky.
- A GT relock (recovery FSM or a `--gtreset`) stops rx_usrclk2 and resets the
  ACLK replica: re-run `arm` after any GT recovery.
- CELLS_LAST far from 10,000,000 means a flaky 10 MHz or PPS line: fix the
  wiring before trusting nanoseconds.
```

- [ ] **Step 8: Re-run the deploy unit tests**

Run: `& .venv\Scripts\python.exe deploy\test_readout_common.py`
Expected: all pass.
Run: `& .venv\Scripts\python.exe deploy\test_wr_time.py`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add deploy/readout_common.py deploy/test_readout_common.py deploy/wr_time.py deploy/test_wr_time.py deploy/tclk_read.py deploy/aclkgt_read.py deploy/wr.md
git commit -m "feat(deploy): WR time tooling (wr_time.py arm/status, sec:ns reader decode via --wr, bring-up runbook)"
```

---

### Task 7: Documentation + full verification sweep

**Files:**
- Modify: `docs/FUNCTIONALITY.md`

**Interfaces:**
- Consumes: everything above.
- Produces: the documented register map and module summary; a clean full-suite run.

- [ ] **Step 1: Document the new modules**

In `docs/FUNCTIONALITY.md`, find the section that describes `global_timebase` (grep `global_timebase`). Immediately after that module's entry, add (matching the file's existing per-module heading style):

```markdown
### wr_timebase / wr_timebase_axi / cdc_word_pulse (White Rabbit timestamps, pipeline build)

The integrated pipeline no longer uses `global_timebase`: `rtl/wr_timebase.sv`
replicas in `clk_40m` and `rx_usrclk2` (plus a monitor in `s_axi_aclk`) each
watch the WR 10 MHz (Pmod1 pin3, E10) and PPS (pin4, E12) and generate
`{sec[31:0], ns[31:0]}` in-domain: ns = 100 ns per 10 MHz edge since PPS plus a
local-clock interpolator cleared at every edge. STRICT validity: ts is 0 unless
armed seconds were loaded at a PPS and both watchdogs are alive; any loss (or a
GT relock, which resets the rx-domain replica) requires the PS to re-arm.
Seconds come from the NTP-synced PS clock via `deploy/wr_time.py arm`.
`rtl/cdc_word_pulse.sv` (toggle-handshake word CDC) carries the arm into each
domain. `rtl/wr_timebase_axi.sv` is the third AXI-Lite slave (S_AXI3 at
0x8002_0000, 16-byte stride): 0x00 STATUS (locked bits, aliveness, arm_pending,
lost_lock sticky), 0x10 SEC_ARM (RW, write arms), 0x20 SEC_NOW / 0x30 NS_NOW
(atomic pair: the SEC_NOW read latches NS_NOW), 0x40 PPS_COUNT, 0x50 CELLS_LAST
(expect 10,000,000), 0x60 CTRL ([0] clear sticky, [1] disarm). Bring-up runbook:
`deploy/wr.md`. Suites: `tb/wr_timebase`, `tb/wr_timebase_axi`,
`tb/cdc_word_pulse`, and the WR-converted `tb/aclk_pipeline_chain`.
```

Also update any sentence in the same file claiming the pipeline timestamps are shared ticks from `global_timebase` to note that the pipeline build now uses the WR timebase (standalone builds still use ticks).

- [ ] **Step 2: Full verification sweep**

Run each and confirm PASS:

```powershell
.\sim.ps1 run -Module cdc_word_pulse
.\sim.ps1 run -Module wr_timebase
.\sim.ps1 run -Module wr_timebase_axi
.\sim.ps1 run -Module aclk_pipeline_chain
.\sim.ps1 run -Module global_timebase
.\sim.ps1 run -Module aclk_readout_ext_ts
.\sim.ps1 run -Module tclk_readout
.\sim.ps1 run -Module aclkgt_readout
& .venv\Scripts\python.exe deploy\test_readout_common.py
& .venv\Scripts\python.exe deploy\test_wr_time.py
& .venv\Scripts\python.exe deploy\test_tclk_filter.py
```

Expected: every suite PASSES. The last three are the deploy-side unit tests (no hardware needed).

- [ ] **Step 3: Commit**

```bash
git add docs/FUNCTIONALITY.md
git commit -m "docs: WR timebase modules, S_AXI3 register map, and bring-up pointers"
```

---

## Post-plan gates (not automated here)

1. Vivado build (Task 5 Step 9) must pass before any hardware claim.
2. On-board bring-up follows `deploy/wr.md` and needs the WR replica generator (sibling project) or a real WR node on Pmod 1 pins 3/4.
3. Hardware acceptance: `wr_time.py status` shows all three locked bits, CELLS_LAST within 10,000,000 +/- 2, and `tclk_read.py --wr` / `aclkgt_read.py --wr` print matching UTC stamps for pipeline events (ACLK a bounded few us after TCLK).
