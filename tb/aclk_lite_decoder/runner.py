"""Cocotb 2.0 Python runner for the ACLK-Lite Manchester decoder (ADM).
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_lite_decoder():
    run_cocotb(
        "aclk_lite_decoder",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/aclk_lite/aclk_lite_decoder.sv",
        ],
        hdl_toplevel="aclk_lite_decoder",
    )


if __name__ == "__main__":
    test_aclk_lite_decoder()
