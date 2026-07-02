"""Cocotb 2.0 runner for rtl/global_timebase.v (shared timestamp).
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_global_timebase():
    run_cocotb(
        "global_timebase",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/global_timebase.v",
        ],
        hdl_toplevel="global_timebase",
    )


if __name__ == "__main__":
    test_global_timebase()
