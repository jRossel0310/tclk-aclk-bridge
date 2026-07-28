"""Cocotb 2.0 Python runner for the full TCLK PL chain (tclk_readout_top): the
inherited biphase-mark receiver (TCLK_RCV = serdec4_9MHz + TCLK_DESERIALIZER2)
feeding the decoder-agnostic AXI4-Lite readout (aclk_readout_axi), end to end.
Shared plumbing: tb/runner_common.py."""
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


# Sources shared by every tclk_readout_top build: the inherited biphase-mark
# receiver + decoder-agnostic AXI readout, plus (since the fine-TDC integration)
# the multiphase fine-TDC and its 5-sample sub-bin decoder.
_TCLK_READOUT_SOURCES = [
    "rtl/synchronizer.sv",
    "rtl/async_fifo.sv",
    "rtl/cdc_gray_count.sv",
    "rtl/aclk_readout/aclk_readout_core.sv",
    "rtl/aclk_readout/aclk_readout_axi.sv",
    "rtl/aclk_bridge/serdec4_9MHz.v",
    "rtl/aclk_bridge/TCLK_DESERIALIZER2.v",
    "rtl/aclk_bridge/TCLK_RCV.v",
    "rtl/aclk_lite/tclk_fine_decode.sv",
    "rtl/aclk_lite/tclk_fine_tdc.sv",
    "rtl/aclk_lite/tclk_readout_top.sv",
]


def test_tclk_readout():
    # USE_EXT_TS left at the module default (0 = internal free-running ts
    # counter): this suite only exercises decode-preservation (event
    # order/codes/counts), not the fine-TDC's frozen_coarse timestamp.
    run_cocotb(
        "tclk_readout",
        sources=_TCLK_READOUT_SOURCES,
        hdl_toplevel="tclk_readout_top",
        parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
    )


def test_tclk_ts_jitter():
    # USE_EXT_TS left at the module default (0): this suite characterizes the
    # internal ts counter's DAVn-latch resync dither, not the fine-TDC.
    run_cocotb(
        "tclk_readout",
        sources=_TCLK_READOUT_SOURCES,
        hdl_toplevel="tclk_readout_top",
        parameters={"OSR": int(os.getenv("TCLK_OSR", "8"))},
        test_module="test_tclk_ts_jitter",
    )


def test_tclk_fine_chain():
    # USE_EXT_TS=1 is REQUIRED here: this is the one suite that actually
    # proves frozen_coarse (the fine-TDC's ref-edge timestamp) reaches the
    # packed event TS. At the default 0, aclk_readout_core uses its own
    # internal free-running counter and the ts_ext(frozen_coarse) wiring
    # added in tclk_readout_top is never exercised.
    run_cocotb(
        "tclk_readout",
        sources=_TCLK_READOUT_SOURCES,
        hdl_toplevel="tclk_readout_top",
        parameters={"OSR": int(os.getenv("TCLK_OSR", "8")), "USE_EXT_TS": 1},
        test_module="test_tclk_fine_chain",
    )


if __name__ == "__main__":
    test_tclk_readout()
    test_tclk_ts_jitter()
    test_tclk_fine_chain()
