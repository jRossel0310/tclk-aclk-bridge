# constraints/

Physical constraints for the **hardware / synthesis** stage (not used by
simulation). Xilinx `.xdc` files map top-level ports to package pins, set I/O
standards, and define timing. Vivado reads these; the cocotb simulation does not.

| File | Target |
|------|--------|
| `kr260.xdc` | Kria KR260 — `serial_in`/`serial_out` on PMOD pins for the `uart_echo_top` bring-up |

> ⚠️ The PMOD package pins in `kr260.xdc` are **starter values** from the KR260
> master pinout. Verify them against the official KR260 master XDC / carrier-card
> schematic (AMD Kria K26 docs) before connecting an adapter to the board.

The scripted Vivado build that consumes this file lives in
[`../vivado/`](../vivado/) (`build.tcl`, run via `hw.ps1` / `hw.sh`).
