"""Cocotb 2.0 Python runner for the inherited TCLK receiver (rtl/aclk_bridge):
TCLK_RCV = serdec4_9MHz (biphase bit recovery) + TCLK_DESERIALIZER2 (byte
assembly + parity). Shared plumbing: tb/runner_common.py."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_tclk_rcv():
    run_cocotb(
        "tclk_rcv",
        sources=[
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_bridge/TCLK_DESERIALIZER2.v",
            "rtl/aclk_bridge/TCLK_RCV.v",
        ],
        hdl_toplevel="TCLK_RCV",
        parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
    )


if __name__ == "__main__":
    test_tclk_rcv()
