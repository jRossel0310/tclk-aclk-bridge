"""Cocotb 2.0 runner for rtl/aclk_gen_bd_top.v (the no-AXI BD wrapper).
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_gen_bd_top():
    run_cocotb(
        "aclk_gen_bd_top",
        sources=[
            "rtl/aclk_lite/aclk_lite_encoder.sv",
            "rtl/aclk_lite/aclk_lite_gen_timeline.sv",
            "rtl/aclk_gen_bd_top.v",
        ],
        hdl_toplevel="aclk_gen_bd_top",
    )


if __name__ == "__main__":
    test_aclk_gen_bd_top()
