# TCLK Configurable Event Filter — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the PS suppress a runtime-configurable set of TCLK event codes (e.g. the 720 Hz `0x07` machine clock) at the hardware FIFO input, while still counting them, so only "real" events are buffered for readout.

**Architecture:** Add a 256-bit drop mask to `aclk_readout_axi`, written one bit at a time via a new `FILTER_CFG` register. The mask is synchronized into the rx clock domain and gates the `aclk_valid` that reaches the FIFO packer: masked, non-null events are not pushed and instead increment a new `FILTERED_COUNT`. Default (mask all-zero) is byte-for-byte the current behavior. Generalizes the existing `DROP_NULL` count-but-drop pattern.

**Tech Stack:** SystemVerilog RTL (Icarus Verilog sim via cocotb 2.0); Python PS reader (`deploy/tclk_read.py`); tests run with `.\sim.ps1 run -Module <name>`.

## Global Constraints

- **Register spacing is 16 bytes** (`rsel = araddr[7:4]`, `waddr = awaddr[7:4]`). All register offsets are multiples of `0x10`. This is a committed hardware workaround; do not revert it.
- **`aclk_readout_axi` is shared** by the ACLK, ACLK-Lite, and TCLK readout tops. Changes must keep all three sims green.
- **Safe default:** `drop_mask` resets to all zeros = drop nothing = unchanged behavior. The filter is inert until the PS writes `FILTER_CFG`.
- **Filter applies to non-null events only:** a `0xFF` null (when `DROP_NULL=1`) is still counted by `NULL_COUNT`, never by `FILTERED_COUNT`.
- **Sim command:** `powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module <module>` (Icarus, the default). A passing run prints `** TEST ... PASS **` per cocotb test and exits 0.
- **All work on branch `tclk-event-filter`** (off `main` @ `4dbb12b`). Commit after each task.

## File Structure

- `rtl/aclk_readout/aclk_readout_axi.sv` — **modify**: add `drop_mask` + `FILTER_CFG` write + mask CDC + `drop_this` valid-gating + `FILTERED_COUNT`; remove the temp `CONST` read. (Single responsibility: the AXI face of the readout — already the right home, mirrors `DROP_NULL`.)
- `tb/aclk_readout_axi/test_aclk_readout_axi.py` — **modify**: 16-byte offsets; add the filter test.
- `tb/tclk_readout/test_tclk_readout.py` — **modify**: 16-byte offsets.
- `tb/aclk_lite_readout/test_aclk_lite_readout.py` — **modify**: 16-byte offsets.
- `deploy/tclk_filter.py` — **create**: pure helpers (`parse_drop_codes`, `filter_cfg_word`), no hardware deps.
- `deploy/test_tclk_filter.py` — **create**: unit tests for the helpers.
- `deploy/tclk_read.py` — **modify**: `--drop` CLI, configure the mask at startup, show `FILTERED_COUNT` in stats.

---

## Task 1: Restore the three readout sims to green under the 16-byte map

The committed 16-byte RTL (`rsel = araddr[7:4]`) broke the existing readout sims, which still read 4-byte offsets. Fix the offset tuples so the suite is green before adding the filter.

**Files:**
- Modify: `tb/aclk_readout_axi/test_aclk_readout_axi.py:30-32`
- Modify: `tb/tclk_readout/test_tclk_readout.py:41-43`
- Modify: `tb/aclk_lite_readout/test_aclk_lite_readout.py:32-34`

**Interfaces:**
- Consumes: nothing (regression fix).
- Produces: a green readout test suite at 16-byte offsets; later tasks add to `tb/aclk_readout_axi/test_aclk_readout_axi.py`.

- [ ] **Step 1: Confirm the sim is currently broken**

Run: `powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_readout_axi`
Expected: FAIL — `axi_read_event` reads the wrong registers (e.g. `EVENT` at `0x04` now decodes to `rsel 0` = STATUS), so an order/data assertion fails.

- [ ] **Step 2: Fix `tb/aclk_readout_axi/test_aclk_readout_axi.py` offsets**

Replace lines 30-32:

```python
# Register byte offsets (16-byte spacing — see aclk_readout_axi.sv).
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90
)
```

- [ ] **Step 3: Fix `tb/tclk_readout/test_tclk_readout.py` offsets**

Replace lines 41-43:

```python
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT, DEBUG = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90, 0xA0
)
```

- [ ] **Step 4: Fix `tb/aclk_lite_readout/test_aclk_lite_readout.py` offsets**

Replace lines 32-34:

```python
STATUS, EVENT, DATA_HI, DATA_LO, TS_HI, TS_LO, POP, EVENT_COUNT, NULL_COUNT, ERROR_COUNT = (
    0x00, 0x10, 0x20, 0x30, 0x40, 0x50, 0x60, 0x70, 0x80, 0x90
)
```

- [ ] **Step 5: Run all three sims to verify they pass**

Run each:
```
powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_readout_axi
powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module tclk_readout
powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_lite_readout
```
Expected: each prints `PASS` for its cocotb tests and exits 0.

- [ ] **Step 6: Commit**

```bash
git add tb/aclk_readout_axi/test_aclk_readout_axi.py tb/tclk_readout/test_tclk_readout.py tb/aclk_lite_readout/test_aclk_lite_readout.py
git commit -m "test: update readout sim offsets to the 16-byte register map"
```

---

## Task 2: Failing cocotb test for the drop-mask filter

Add a test that programs the mask to drop one code and keep another, then asserts the drop never reaches the FIFO and `FILTERED_COUNT` ticks. It fails now because `FILTER_CFG`/`FILTERED_COUNT` and the filter don't exist yet.

**Files:**
- Modify: `tb/aclk_readout_axi/test_aclk_readout_axi.py` (add constants + one test)

**Interfaces:**
- Consumes: `axi_read`, `axi_write` (from `axi_lite_bfm`), `stream_frames` (from `aclk_tx_model`), and the module-level helpers `_reset`, `_idle_carrier`, `axi_read_event`, `STATUS`, `EVENT_COUNT` already in this file.
- Produces: the test `test_event_filter_drop`, which Task 3's RTL must make pass.

- [ ] **Step 1: Add filter register offsets near the other offsets**

After the offset tuple (the lines edited in Task 1, ~line 32), add:

```python
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0   # drop-mask config (write-only); dropped-event count (read)
```

- [ ] **Step 2: Add the failing test at the end of the file**

```python
@cocotb.test()
async def test_event_filter_drop(dut):
    """Program the drop-mask to suppress code 0x01: it must never reach the FIFO
    and must increment FILTERED_COUNT, while the unmasked code 0xA5 still reads
    out and increments EVENT_COUNT."""
    cocotb.start_soon(Clock(dut.CLK1, RX_PERIOD_NS, unit="ns").start())
    cocotb.start_soon(Clock(dut.s_axi_aclk, AXI_PERIOD_NS, unit="ns").start())
    await _reset(dut)

    # Drop low-byte code 0x01 (bit8 = drop). Keep 0xA5.
    await axi_write(dut, FILTER_CFG, 0x100 | 0x01)

    drop_evt = (0x0001, 0x1111222233334444)
    keep_evt = (0x00A5, 0xAAAABBBBCCCCDDDD)
    await stream_frames(dut, [drop_evt, keep_evt], repeat=10)

    stop = {"done": False}
    cocotb.start_soon(_idle_carrier(dut, stop))     # keep the link aligned during readout
    await ClockCycles(dut.CLK1, 8)
    await ClockCycles(dut.s_axi_aclk, 6)

    collected = []
    while True:
        status = await axi_read(dut, STATUS)
        if status & 0x1:                            # empty
            break
        collected.append(await axi_read_event(dut))
    stop["done"] = True

    assert collected, "no events read over AXI"
    for ev, da, ts in collected:
        assert (ev & 0xFF) == 0xA5, f"dropped code 0x01 leaked into the FIFO: 0x{ev:04X}"

    filtered = await axi_read(dut, FILTERED_COUNT)
    ev_count = await axi_read(dut, EVENT_COUNT)
    assert filtered > 0, "FILTERED_COUNT did not register the dropped 0x01 events"
    assert ev_count == len(collected), \
        f"EVENT_COUNT {ev_count} != kept events read {len(collected)}"
    dut._log.info(
        f"filter OK: dropped 0x01 (FILTERED_COUNT={filtered}), kept {len(collected)} x 0xA5"
    )
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_readout_axi`
Expected: `test_event_filter_drop` FAILS — `FILTERED_COUNT` (offset `0xE0`) reads 0 (no such register yet) so `assert filtered > 0` fails, and/or the `0x01` events still appear in the FIFO. (The other tests still pass.)

- [ ] **Step 4: Commit**

```bash
git add tb/aclk_readout_axi/test_aclk_readout_axi.py
git commit -m "test: failing test for the configurable event drop-mask"
```

---

## Task 3: Implement the drop-mask filter in `aclk_readout_axi`

**Files:**
- Modify: `rtl/aclk_readout/aclk_readout_axi.sv`

**Interfaces:**
- Consumes: `synchronizer` (`rtl/synchronizer.sv`, params `WIDTH`, `STAGES`), `cdc_gray_count` (`rtl/cdc_gray_count.sv`, param `W`), `aclk_readout_core` (its `aclk_valid` input is the FIFO push gate).
- Produces: `FILTER_CFG` write register at `0xD0` (format `{bit8=drop, bits[7:0]=code}`); `FILTERED_COUNT` read register at `0xE0`.

- [ ] **Step 1: Add the drop mask + its rx-domain sync**

In `aclk_readout_axi.sv`, just **after** the `lock_sync` block (right before the `AXI4-Lite read channel` comment), add:

```systemverilog
    // ---------------------------------------------------------------
    // Configurable event drop-mask. One bit per event code (0x00-0xFF): a set bit
    // means "do not push this code to the FIFO; count it in FILTERED_COUNT instead."
    // Reset = all zeros = drop nothing = original behavior. Written via FILTER_CFG
    // (0xD0) one bit at a time, so at most one bit changes between writes -- the
    // 2-FF per-bit sync into rx_clk is safe (single-bit, gray-like). Quasi-static
    // config: a transient during a mid-run change at worst mis-filters one event.
    // ---------------------------------------------------------------
    logic [255:0] drop_mask;                 // s_axi_aclk domain (written by the write FSM)
    wire  [255:0] drop_mask_rx;
    synchronizer #(.WIDTH(256), .STAGES(2)) u_mask_sync (
        .clk          (rx_clk),
        .async_signal (drop_mask),
        .sync_signal  (drop_mask_rx)
    );

    wire drop_this  = drop_mask_rx[aclk_event[7:0]] && !core_is_null;  // never drop a null
    wire core_valid = aclk_valid && !drop_this;     // gated valid into the FIFO packer

    wire [31:0] filtered_count;
    cdc_gray_count #(.W(32)) u_cnt_filt (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(aclk_valid && drop_this),
        .dst_clk(s_axi_aclk), .count_dst(filtered_count));
```

- [ ] **Step 2: Gate the core's valid with `core_valid`**

In the `aclk_readout_core` instantiation (`u_core`), change the `aclk_valid` connection:

```systemverilog
        .aclk_valid   (core_valid),
```

(was `.aclk_valid (aclk_valid)`). Dropped events now never reach the FIFO.

- [ ] **Step 3: Make `EVENT_COUNT` count only kept events**

Find the existing counter feed (`wire push_evt = aclk_valid && !core_is_null;`) and change it to gate on `core_valid`:

```systemverilog
    wire push_evt   = core_valid && !core_is_null;   // events actually pushed (kept)
    wire push_null  = aclk_valid && core_is_null;
```

(`push_null` is unchanged; leave the existing `u_cnt_evt` / `u_cnt_null` instances as-is — they consume `push_evt` / `push_null`.)

- [ ] **Step 4: Latch write data and apply `FILTER_CFG` in the write FSM**

In the write-channel declarations, add a write-data latch next to `waddr_q`:

```systemverilog
    logic [31:0] wdata_q;                    // latched write data (W may precede/follow AW)
```

In the write `always_ff` reset branch, add:

```systemverilog
            drop_mask <= '0;
            wdata_q   <= '0;
```

In the W-handshake line, also latch the data:

```systemverilog
            if (s_axi_wvalid && wready_r) begin
                wready_r <= 1'b0;
                wdata_q  <= s_axi_wdata;
            end
```

In the both-landed branch (where POP is decided), add the `FILTER_CFG` write (`rsel 13` = `0xD0`):

```systemverilog
            if (!awready_r && !wready_r && !bvalid_r) begin
                bvalid_r <= 1'b1;
                if (waddr_q == 'd6 && !empty)
                    pop <= 1'b1;                       // POP @ 0x60
                if (waddr_q == 'd13)
                    drop_mask[wdata_q[7:0]] <= wdata_q[8];  // FILTER_CFG @ 0xD0
            end
```

- [ ] **Step 5: Swap the temp `CONST` read for `FILTERED_COUNT`**

In the read FSM `case (rsel)`, remove the `CONST` line and add `FILTERED_COUNT` at `rsel 14` (`0xE0`):

```systemverilog
                'd12: rdata_r <= {31'b0, lock_sync[1]}; // 0xC0: MMCM locked (synced)
                'd14: rdata_r <= filtered_count;        // 0xE0: events dropped by the mask
                default: rdata_r <= 32'b0;
```

(The old `'d13: rdata_r <= 32'hC0FFEE00;` line is deleted — `0xD0` is now write-only `FILTER_CFG`.)

- [ ] **Step 6: Update the register-map comment header**

In the block comment near the top, replace the `0xD0 CONST` line with:

```systemverilog
//   0xD0 FILTER_CFG    WO  {bit8=drop, bits[7:0]=code} -> set/clear a drop_mask bit
//   0xE0 FILTERED_COUNT RO events dropped by the mask (not pushed to the FIFO)
```

- [ ] **Step 7: Run the filter test — it should pass**

Run: `powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_readout_axi`
Expected: `test_event_filter_drop` now PASSES, and `test_axi_event_readout` / `test_axi_error_count` still PASS (mask is zero in those, so behavior is unchanged).

- [ ] **Step 8: Run the other two readout sims — no regressions**

```
powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module tclk_readout
powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_lite_readout
```
Expected: both PASS (they don't write `FILTER_CFG`, so `drop_mask=0` and nothing changes).

- [ ] **Step 9: Commit**

```bash
git add rtl/aclk_readout/aclk_readout_axi.sv
git commit -m "feat: configurable event drop-mask (FILTER_CFG + FILTERED_COUNT)"
```

---

## Task 4: PS reader — `--drop` config + `FILTERED_COUNT` in stats

**Files:**
- Create: `deploy/tclk_filter.py`
- Create: `deploy/test_tclk_filter.py`
- Modify: `deploy/tclk_read.py`

**Interfaces:**
- Produces: `parse_drop_codes(s) -> list[int]` and `filter_cfg_word(code, drop=True) -> int`, imported by `tclk_read.py`.
- Consumes: `tclk_read.py`'s existing `wr(off, val)` helper and `FILTER_CFG`/`FILTERED_COUNT` offsets.

- [ ] **Step 1: Write the failing helper unit test**

Create `deploy/test_tclk_filter.py`:

```python
"""Unit tests for the pure event-filter helpers (no hardware needed).
Run: python deploy/test_tclk_filter.py"""
from tclk_filter import parse_drop_codes, filter_cfg_word


def test_parse():
    assert parse_drop_codes("") == []
    assert parse_drop_codes(None) == []
    assert parse_drop_codes("07") == [0x07]
    assert parse_drop_codes("07,0F,BA,8F") == [0x07, 0x0F, 0xBA, 0x8F]
    assert parse_drop_codes(" 07 , 0f ") == [0x07, 0x0F]   # whitespace + lowercase


def test_cfg_word():
    assert filter_cfg_word(0x07) == 0x107
    assert filter_cfg_word(0x07, drop=True) == 0x107
    assert filter_cfg_word(0x07, drop=False) == 0x007
    assert filter_cfg_word(0xBA) == 0x1BA


if __name__ == "__main__":
    test_parse()
    test_cfg_word()
    print("OK")
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python deploy/test_tclk_filter.py`
Expected: FAIL — `ModuleNotFoundError: No module named 'tclk_filter'`.
(If `tclk_filter` resolves elsewhere, it fails with `ImportError` on the functions.)

- [ ] **Step 3: Write the helpers**

Create `deploy/tclk_filter.py`:

```python
"""Pure helpers for the TCLK readout event drop-filter (no hardware deps, so they
are unit-testable off the board). Used by tclk_read.py."""


def parse_drop_codes(spec):
    """Parse a comma-separated list of hex event codes into a list of ints.
    '' / None -> []. '07,0F,BA' -> [0x07, 0x0F, 0xBA]. Whitespace tolerant."""
    spec = (spec or "").strip()
    if not spec:
        return []
    return [int(tok, 16) & 0xFF for tok in spec.split(",") if tok.strip()]


def filter_cfg_word(code, drop=True):
    """FILTER_CFG write word: bit8 = drop?, bits[7:0] = event code."""
    return (0x100 if drop else 0x000) | (code & 0xFF)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python deploy/test_tclk_filter.py`
Expected: prints `OK`, exits 0.

- [ ] **Step 5: Commit the helpers**

```bash
git add deploy/tclk_filter.py deploy/test_tclk_filter.py
git commit -m "feat: tclk_filter helpers (parse_drop_codes, filter_cfg_word) + tests"
```

- [ ] **Step 6: Wire the helpers into `tclk_read.py` — offsets + import**

In `deploy/tclk_read.py`, change the `CONST` constant line to the two filter registers:

```python
HEARTBEAT, LOCK = 0xB0, 0xC0   # free-running clk_40m counter (trust check); MMCM-locked bit
FILTER_CFG, FILTERED_COUNT = 0xD0, 0xE0   # drop-mask config (write); dropped-event count (read)
```

Update the `NAME` dict: remove the `CONST: "CONST"` entry and add:

```python
        HEARTBEAT: "HEARTBEAT", LOCK: "LOCK",
        FILTER_CFG: "FILTER_CFG", FILTERED_COUNT: "FILTERED_COUNT"}
```

Add the import near the top (after the `import` line, before `DEV = ...`):

```python
from tclk_filter import parse_drop_codes, filter_cfg_word
```

- [ ] **Step 7: Parse `--drop` and configure the mask at startup**

Replace the `DEV = sys.argv[1] ...` line with argv parsing that also pulls `--drop`:

```python
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
```

After the `# mmap ok ...` line and the watchdog start (just before `say("# streaming ...")`), configure the drops:

```python
for _c in DROP_CODES:
    wr(FILTER_CFG, filter_cfg_word(_c))
if DROP_CODES:
    say("# drop-mask: suppressing " + ", ".join("0x%02X" % c for c in DROP_CODES))
```

- [ ] **Step 8: Show `FILTERED_COUNT` in the stats line, drop the dead `CONST` probe**

In `stats_line()`, add the filtered count:

```python
def stats_line():
    dbg = rd(DEBUG)
    return "[stats] EVT=%d NULL=%d ERR=%d FILT=%d | tclk_edges=%d level=%d sig_err=%d | hb=%d lock=%d" % (
        rd(EVENT_COUNT), rd(NULL_COUNT), rd(ERROR_COUNT), rd(FILTERED_COUNT),
        dbg & 0x3FFFFFFF, (dbg >> 30) & 1, (dbg >> 31) & 1,
        rd(HEARTBEAT), rd(LOCK) & 1)
```

In `probe()`, delete the `CONST` block (the lines reading `CONST (0xD0)` and the `READ PATH OK` / `WARNING: CONST mismatch` branch) — `0xD0` is now write-only, so reading it returns 0. Leave the rest of `probe()` unchanged.

- [ ] **Step 9: Syntax-check `tclk_read.py`**

Run: `.\.venv\Scripts\python.exe -m py_compile deploy\tclk_read.py`
Expected: no output, exit 0 (compiles). (Full behavior needs the board; this confirms it parses and the imports resolve.)

- [ ] **Step 10: Commit**

```bash
git add deploy/tclk_read.py
git commit -m "feat: tclk_read.py --drop event filter + FILTERED_COUNT stat"
```

---

## Done / hardware verification (manual, off-plan)

After the plan is implemented and committed, rebuild + load on the KR260 and run:

```
sudo python3 tclk_read.py /dev/uio4 --drop 07,0F,BA,8F
```

Expect the `0x07`/`0x0F`/`0xBA`/`0x8F` events to vanish from the stream, `EVT` to climb only with real events, `FILT` to climb at ~720 Hz, and `STATUS` overflow (bit1) to stop setting. Copy `deploy/tclk_filter.py` to the board alongside `tclk_read.py`.

## Self-Review

- **Spec coverage:** drop mask (T3), `FILTER_CFG` (T3), `FILTERED_COUNT` (T3), filter logic at FIFO input (T3 valid-gating), mask CDC (T3 synchronizer), `EVENT_COUNT`=kept (T3 step 3), FIFO depth unchanged (no task changes it ✓), `CONST` removed / `0xD0` reused (T3 step 5/6), `tclk_read.py` `--drop` + stats (T4), cocotb filter test (T2), existing sim offsets fixed (T1). All spec sections map to a task.
- **Placeholder scan:** none — every code step shows complete code.
- **Type/name consistency:** `FILTER_CFG=0xD0`, `FILTERED_COUNT=0xE0`, `filter_cfg_word`, `parse_drop_codes`, `drop_mask`, `drop_mask_rx`, `drop_this`, `core_valid`, `filtered_count`, `wdata_q` used identically across RTL, tests, and PS code; FILTER_CFG format `{bit8=drop, bits[7:0]=code}` consistent in RTL (T3), test (`0x100|0x01`, T2), and `filter_cfg_word` (T4).
