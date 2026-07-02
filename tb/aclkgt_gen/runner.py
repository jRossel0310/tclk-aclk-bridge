"""Cocotb 2.0 runner for rtl/aclk_gt/aclk_gt_frame_gen.v.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclkgt_gen():
    run_cocotb(
        "aclkgt_gen",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/gearbox_96_to_16.v",
            "rtl/aclk_gt/aclk_gt_frame_gen.v",
        ],
        hdl_toplevel="aclk_gt_frame_gen",
    )


if __name__ == "__main__":
    test_aclkgt_gen()
