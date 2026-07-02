"""Cocotb 2.0 runner for the aclk_lite_bridge -> aclk_lite_encoder -> clk_rcv chain.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_lite_bridge():
    run_cocotb(
        "aclk_lite_bridge",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/aclk_lite_bridge.v",
            "rtl/aclk_lite/aclk_lite_encoder.sv",
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_lite/clk_byte_framer.sv",
            "rtl/aclk_lite/clk_rcv.sv",
            "tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv",
        ],
        hdl_toplevel="tb_aclk_lite_bridge_top",
    )


if __name__ == "__main__":
    test_aclk_lite_bridge()
