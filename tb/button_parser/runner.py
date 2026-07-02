"""Cocotb 2.0 Python runner for the button_parser testbench.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_button_parser():
    run_cocotb(
        "button_parser",
        sources=[
            "rtl/button_parser.sv",
            "rtl/synchronizer.sv",
            "rtl/debouncer.sv",
            "rtl/edge_detector.sv",
        ],
        hdl_toplevel="button_parser",
        parameters={"SAMPLE_CNT_MAX": 4, "PULSE_CNT_MAX": 4},
    )


if __name__ == "__main__":
    test_button_parser()
