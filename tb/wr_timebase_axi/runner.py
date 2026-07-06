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
