# constraints/

Physical constraints for the **hardware / synthesis** stage (not used by
simulation). Xilinx `.xdc` files map top-level ports to package pins, set I/O
standards, and define timing. Vivado reads these; the cocotb simulation does not.

| File | Target |
|------|--------|
| `kr260_aclk_pipeline.xdc` | Kria KR260: pin/timing constraints for the single-board TCLK -> ACLK pipeline (TCLK on a PMOD pin, GT/SFP sideband ports, PL clocking) |

> ⚠️ The PMOD/SFP package pins in `kr260_aclk_pipeline.xdc` are **starter values**
> from the KR260 master pinout. Verify them against the official KR260 master XDC /
> carrier-card schematic (AMD Kria K26 docs) before connecting an adapter to the board.

The scripted Vivado build that consumes this file lives in
[`../vivado/`](../vivado/) (`build_aclk_pipeline.tcl`, run via `hw.ps1` / `hw.sh`
with `KRIA_BUILD_DIR` honored for the output location).
