"""Cocotb 2.0 runner for the ACLK-Lite generator -> unified clk_rcv loopback.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_lite_gen_loopback():
    run_cocotb(
        "aclk_lite_gen_loopback",
        sources=[
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_lite/clk_byte_framer.sv",
            "rtl/aclk_lite/clk_rcv.sv",
            "rtl/aclk_lite/aclk_lite_encoder.sv",
            "rtl/aclk_lite/aclk_lite_gen_timeline.sv",
            "tb/aclk_lite_gen_loopback/tb_aclk_gen_loopback.sv",
        ],
        hdl_toplevel="tb_aclk_gen_loopback",
        test_module="test_aclk_gen_loopback",
    )


if __name__ == "__main__":
    test_aclk_lite_gen_loopback()
