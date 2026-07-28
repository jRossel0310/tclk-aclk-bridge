"""Cocotb 2.0 Python runner for the REF_EDGE frame-detection strobe: same DUT
stack as tb/tclk_rcv (serdec4_9MHz + TCLK_DESERIALIZER2 + TCLK_RCV), just
asserting on the new REF_EDGE port instead of (or alongside) DAVn/DATA.
Shared plumbing: tb/runner_common.py."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_tclk_refedge():
    run_cocotb(
        "tclk_refedge",
        sources=[
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_bridge/TCLK_DESERIALIZER2.v",
            "rtl/aclk_bridge/TCLK_RCV.v",
        ],
        hdl_toplevel="TCLK_RCV",
        parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
    )


if __name__ == "__main__":
    test_tclk_refedge()
