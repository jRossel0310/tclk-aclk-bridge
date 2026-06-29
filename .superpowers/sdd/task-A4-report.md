# Task A4 Report: `aclk_lite_bridge`

## What was built

- `rtl/aclk_lite_bridge.v` - the bridge module (verbatim from the brief spec), wiring
  decoded ACLK events through an `async_fifo` (WIDTH=80, ADDR_WIDTH=4) for CDC from
  `rx_clk` to `enc_clk`, then a 4-state FSM (IDLE/LATCH/START/WAIT) that pops one entry
  and pulses `enc_start` when `!enc_busy`, outputting `enc_frame_type=2` (full 12-byte
  packet) always. Null events (`aclk_event[7:0]==0xFF`) are filtered before FIFO write.

- `tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv` - testbench top wiring
  `aclk_lite_bridge` -> `aclk_lite_encoder` (SAMPLES_PER_CELL=8) -> `clk_rcv`. Exposes
  `enc_line` as an output port for the plot sampler.

- `tb/aclk_lite_bridge/test_aclk_lite_bridge.py` - two tests:
  1. `test_real_event_recovered`: drives event_id=0x1234 / data=0xDEADBEEFCAFE0001 on rx
     side, asserts enc_start fires, then asserts clk_rcv recovers exact event_id and data.
  2. `test_null_event_suppressed`: drives aclk_event=0x00FF (null), asserts enc_start
     stays low for 300 enc_clk cycles.
  Emits a step plot of enc_line to `sim_build/aclk_lite_bridge/plots/enc_line.png`.

- `tb/aclk_lite_bridge/runner.py` - standard cocotb runner, sources as specified in brief.

## TDD RED/GREEN

**RED** (before `rtl/aclk_lite_bridge.v` existed):
```
C:\...\rtl\aclk_lite_bridge.v: No such file or directory
simulation failed (exit 1)
```

**GREEN** (after implementation):
```
 5576.00ns INFO  enc_start asserted - bridge dispatched the event to the encoder
17876.00ns INFO  END-TO-END OK: event_id=0x1234  data=0xDEADBEEFCAFE0001
17876.00ns INFO  test_real_event_recovered passed
27139.50ns INFO  null-event suppression OK: enc_start stayed low for 300 cycles
27139.50ns INFO  test_null_event_suppressed passed

TESTS=2 PASS=2 FAIL=0 SKIP=0   (27139.50 ns sim time, exit 0)
```

## Files changed

- Created: `rtl/aclk_lite_bridge.v`
- Created: `tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv`
- Created: `tb/aclk_lite_bridge/test_aclk_lite_bridge.py`
- Created: `tb/aclk_lite_bridge/runner.py`

## FSM handshake tuning

No tuning was needed. The S_WAIT exit condition `enc_busy==0 && enc_start==0` works
correctly against the encoder's real timing: the encoder asserts `busy` (via the
`pending` latch) at the next cell boundary after it sees `start`, so by the time the
FSM reaches S_WAIT on the cycle after S_START, `enc_start` has gone to 0 and the
encoder has latched `pending=1`. S_WAIT then correctly waits until `busy` goes low at
frame completion. No double-dispatch observed.

## Pre-existing warnings (not our code)

Icarus 14 emits "sorry: constant selects in always_* processes" on
`rtl/aclk_lite/aclk_lite_encoder.sv` lines 41-59. These are pre-existing warnings from
the iterator-indexed always_comb in the encoder and do not affect simulation correctness.
They appear in all existing testbenches that compile the encoder.

## Self-review

- Completeness: bridge module + encode->decode end-to-end recovery test + null-suppression
  check + matplotlib plot. All four deliverables present.
- Quality: no em dashes, reuses `async_fifo` and `aclk_lite_encoder` without modification,
  module code matches brief exactly.
- Discipline: no overbuild. No new RTL infrastructure beyond what the brief specifies.
- Testing: asserts real end-to-end recovery (event_id AND data AND data_valid), not just
  enc_start. Null suppression checked over 300 cycles (> one full frame time).
- Output: sim exits 0, no DeprecationWarnings after fix.

## Concerns

None. The S_WAIT handshake worked on the first attempt against the real encoder timing.
