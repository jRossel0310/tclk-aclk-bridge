"""Cocotb 2.0 runner for the full pure-RTL TCLK->ACLK pipeline chain sim.
Shared plumbing: tb/runner_common.py."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from runner_common import run_cocotb


def test_aclk_pipeline_chain():
    run_cocotb(
        "aclk_pipeline_chain",
        sources=[
            # Shared primitives
            "rtl/synchronizer.sv",
            "rtl/async_fifo.sv",
            "rtl/cdc_gray_count.sv",

            # WR timebase (shared timeline, per-domain replicas + AXI monitor)
            "rtl/cdc_word_pulse.sv",
            "rtl/wr_timebase.sv",
            "rtl/wr_timebase_axi.sv",

            # TCLK_RCV (biphase-mark receiver)
            "rtl/aclk_bridge/serdec4_9MHz.v",
            "rtl/aclk_bridge/TCLK_DESERIALIZER2.v",
            "rtl/aclk_bridge/TCLK_RCV.v",

            # Readout AXI blocks
            "rtl/aclk_readout/aclk_readout_core.sv",
            "rtl/aclk_readout/aclk_readout_axi.sv",

            # TCLK readout top (readout #1)
            "rtl/aclk_lite/tclk_readout_top.sv",

            # ACLK TX encoder path
            "rtl/aclk_bridge/crc8_calc.v",
            "rtl/aclk_bridge/gearbox_96_to_16.v",
            "rtl/aclk_gt/aclk_tclk_encoder.v",

            # ACLK RX decoder path (ACLK_RCV = ACLK_REV)
            "rtl/aclk_bridge/GEARBOX_16_TO_96.v",
            "rtl/aclk_bridge/ACLK_REV.v",

            # ACLK GT readout top (readout #2)
            "rtl/aclk_gt/aclk_gt_readout_top.sv",

            # Testbench top
            "tb/aclk_pipeline_chain/tb_aclk_pipeline_chain_top.sv",
        ],
        hdl_toplevel="tb_aclk_pipeline_chain_top",
    )


if __name__ == "__main__":
    test_aclk_pipeline_chain()
