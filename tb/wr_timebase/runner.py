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
