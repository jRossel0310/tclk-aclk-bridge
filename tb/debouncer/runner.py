"""Cocotb 2.0 Python runner for the debouncer testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_debouncer():
    run_cocotb(
        "debouncer",
        sources=["rtl/debouncer.sv"],
        hdl_toplevel="debouncer",
        parameters={"SAMPLE_CNT_MAX": 4, "PULSE_CNT_MAX": 4},
    )


if __name__ == "__main__":
    test_debouncer()
