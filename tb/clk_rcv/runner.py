"""Cocotb 2.0 runner for the unified ACLK/TCLK decoder rtl/aclk_lite/clk_rcv
(serdec4_9MHz + clk_byte_framer). The line is driven by the real-framing model in
tb/clk_tx_model.py. Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_clk_rcv():
    run_cocotb(
        "clk_rcv",
        sources=[
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_lite/clk_byte_framer.sv",
            "rtl/aclk_lite/clk_rcv.sv",
        ],
        hdl_toplevel="clk_rcv",
    )


if __name__ == "__main__":
    test_clk_rcv()
