"""Cocotb 2.0 runner for the aclk_gt_frame_gen -> ACLK_RCV loopback sim.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclkgt_gen_loop():
    run_cocotb(
        "aclkgt_gen_loop",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/gearbox_96_to_16.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
            "rtl/aclk_gt/aclk_gt_frame_gen.v",
            "tb/aclkgt_gen_loop/tb_aclkgt_gen_loop_top.sv",
        ],
        hdl_toplevel="tb_aclkgt_gen_loop_top",
    )


if __name__ == "__main__":
    test_aclkgt_gen_loop()
