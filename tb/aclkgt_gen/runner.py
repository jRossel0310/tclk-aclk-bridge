import os, sys
from pathlib import Path
from cocotb_tools.runner import get_runner
SIM = os.getenv("SIM", "icarus")
TB_DIR = Path(__file__).resolve().parent
PROJ_DIR = TB_DIR.parents[1]
RTL_DIR = PROJ_DIR / "rtl"
BUILD = PROJ_DIR / "sim_build" / "aclkgt_gen"
sys.path.insert(0, str(TB_DIR)); sys.path.insert(0, str(TB_DIR.parent))
_oss = os.getenv("OSS_CAD_SUITE")
if _oss and (Path(_oss) / "bin").is_dir():
    os.environ["PATH"] = str(Path(_oss) / "bin") + os.pathsep + os.environ.get("PATH", "")
def test_aclkgt_gen():
    runner = get_runner(SIM)
    runner.build(sources=[
        RTL_DIR / "aclk_bridge" / "crc8_calc.v",
        RTL_DIR / "aclk_bridge" / "gearbox_96_to_16.v",
        RTL_DIR / "aclk_gt" / "aclk_gt_frame_gen.v",
    ], hdl_toplevel="aclk_gt_frame_gen", build_dir=BUILD,
       timescale=("1ns", "1ps"), waves=True, always=True)
    runner.test(hdl_toplevel="aclk_gt_frame_gen",
                test_module="test_aclkgt_gen", build_dir=BUILD, waves=True)
if __name__ == "__main__":
    test_aclkgt_gen()
