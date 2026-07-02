"""Cocotb 2.0 Python runner for the external-timestamp (USE_EXT_TS=1) test.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_readout_ext_ts():
    run_cocotb(
        "aclk_readout_ext_ts",
        sources=[
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",
            "tb/aclk_readout_ext_ts/tb_ext_ts_top.sv",
        ],
        hdl_toplevel="tb_ext_ts_top",
        test_module="test_ext_ts",
    )


if __name__ == "__main__":
    test_aclk_readout_ext_ts()
