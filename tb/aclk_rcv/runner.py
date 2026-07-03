"""Cocotb 2.0 Python runner for the ACLK_RCV decoder testbench.

GEARBOX_16_TO_96 intentionally leaves its k_a register unreset, so Icarus
(X warm-up) and Verilator (2-state 0 init) can differ in the pre-alignment
cycles; output correctness is unaffected (the decoder only trusts CRC==0),
but Verilator is unverified for this DUT.

Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_rcv():
    run_cocotb(
        "aclk_rcv",
        sources=[
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",
        ],
        hdl_toplevel="ACLK_RCV",
    )


if __name__ == "__main__":
    test_aclk_rcv()
