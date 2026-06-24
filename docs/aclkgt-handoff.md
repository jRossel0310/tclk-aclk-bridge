# Gigabit-ACLK-over-SFP bring-up: debugging handoff

Self-contained state for picking up the two-board SFP link debug in a fresh context.

## Project goal
Receive the Fermilab "gigabit ACLK" serial timing link over the KR260's SFP+ optical
cage (PL GTH transceiver), decode it, and stream events to the PS (Linux) over AXI-Lite,
mirroring the shipped TCLK and ACLK-Lite readouts (which use a 3.3V pin, NOT the SFP).

- Repo: `c:\Users\jacob\Fermilab\Summer-2026\kria-2-hardware`, branch `aclkgt-readout` (not merged).
- Vivado 2024.2, Windows 11, PowerShell + Git Bash. Two KR260 boards, Finisar
  FTLF1318P3BTL SFP modules (1310nm single-mode, 1.25/2.125G, 10km), single-mode (yellow) fiber.

## Hardware facts (KR260 SFP / GTH)
- Part `xck26-sfvc784-2LV-c`. GTH (GTHE4) bank 224, Quad X0Y1 / Lane X0Y6.
- SFP serial: TX R4/R3, RX T2/T1. Refclk MGTREFCLK0_224 on Y6/Y5 @ 156.25 MHz.
- 1.25 Gbps, 8b/10b, 16-bit user data, QPLL0, K28.5 comma (0xBC). rx_usrclk2 = 62.5 MHz.
- Readout AXI base 0x8000_0000 (LPD) = `/dev/uio4`. Registers spaced 16 BYTES apart
  (hardware quirk: 4-byte-spaced regs read back 0).

## Architecture
- Decoder `rtl/aclk_bridge/ACLK_REV.v` (`ACLK_RCV`): GT 16b + 2b-K stream -> EVENT[15:0]
  + DATA[63:0] + VALID/ERROR. 96-bit frame `{0xBC, EVENT, DATA, CRC8}`, CRC8 poly 0x2F,
  decode gated on CRC==0. Reused UNMODIFIED.
- Generator `rtl/aclk_gt/aclk_gt_frame_gen.v` (IP-free): 3 events cycling
  (0x0001/0x1111222233334444, 0x00A5/0xAAAABBBBCCCCDDDD, 0x1000/0x0123456789ABCDEF),
  free-running (~6M frames/s), no inter-frame gap.
- Readout `rtl/aclk_readout/aclk_readout_axi.sv` (timestamp + dual-clock async FIFO +
  AXI4-Lite), wrapper `rtl/aclk_gt/aclk_gt_readout_top.sv` (DROP_NULL=1).
- GT IP `vivado/ip/gen_aclkgt_gt.tcl` (gtwizard_ultrascale) with STAGED set_property +
  get_property hard-fail verify (GT IP set_property FAILS SILENTLY).

## Build targets (all `design_name=uart_echo_bd` so the overlay/UIO node is identical)
- **M0** `build_aclkgt_loop.tcl` -> `aclk_gt_loop_bd_top.v`: generator + GT near-end PMA
  loopback (internal, no optics) + readout. HARDWARE-PROVEN WORKING. Known-good baseline.
- **M1** `build_aclkgt_rx.tcl` -> `aclk_gt_rx_bd_top.v`: receiver, real SFP RX, TX idle.
- **M2** `build_aclkgt_gen.tcl` -> `aclk_gt_gen_bd_top.v`: pure transmitter (board B,
  bitstream md5 `5f3d641943359900267e159e39068a39`, not rebuilt).
- **selftest** `build_aclkgt_selftest.tcl` -> `aclk_gt_selftest_bd_top.v`: M1 receiver PLUS
  a generator on the SAME board's real SFP TX. Operator loops one fiber from this board's
  SFP TX port to its own RX port -> the board receives its OWN signal over real optics.

## Build / deploy
```
.\hw.ps1 build -Tcl vivado\build_aclkgt_<x>.tcl -Name aclkgt_<x>
```
Output: `build/kria/aclkgt_<x>/aclkgt_<x>.runs/impl_1/uart_echo_bd_wrapper.bit.bin`.
Timing: grep `Estimated Timing Summary` (want WNS positive) + `All user specified timing
constraints are met` in the `*_timing_summary_routed.rpt`.
Deploy: `scp` the `.bit.bin` + `deploy/aclkgt_read.py` + `deploy/tclk_filter.py` to the
board. On board: `md5sum`, `sudo xmutil unloadapp`, `sudo fpgautil -b ~/...bit.bin -o
uart_echo.dtbo`, `sudo python3 -u aclkgt_read.py /dev/uio4 [--gtctrl 0xNN]`.
Reader deps: only `aclkgt_read.py` + `tclk_filter.py` (rest is Python 3 stdlib).

## Environment gotchas (will bite you)
1. Vivado IP-Integrator flake: build dies at "couldn't read C:\Xilinx\...\init.tcl: No
   error" + "::xgui::utils::init_utils invalid command". Cause = Windows Defender
   real-time protection locking Vivado tcl files. FIX (ADMIN PowerShell):
   `Set-MpPreference -DisableRealtimeMonitoring $true`; verify
   `(Get-MpComputerStatus).RealTimeProtectionEnabled` == False. A REBOOT re-enables it.
   The agent shell is non-elevated and CANNOT do this; the USER must.
2. GT IP set_property silently defaults; always staged set + read-back verify.
3. Run long builds in the controller's persistent session, not a subagent.

## Reader stats line
```
[stats] EVT=.. NULL=.. ERR=.. FILT=.. | commadet=.. disperr=.. bb_done=.. bb_error=..
        byteali=.. rcv_aligned=.. | dbg=0x........ lock=..
```
DEBUG word (0xA0) = `{ rx_aligned[31], byteali[30], [29:28]=0, disperr_cnt[27:14],
commadet_cnt[13:0] }`. **IGNORE bb_done/bb_error** (read 0; stale labels from the reverted
buffer-bypass experiment). Counters are 14-bit and WRAP at 16384 — judge by "climbing into
thousands" vs "near 0 / frozen", not a single value.
Runtime GT control `GT_CTRL=0xF0`: [0]=rxpolarity [1]=txpolarity [4:2]=loopback_in
[8]=RX-datapath re-init pulse. `--gtctrl 0xNN` writes it with the re-init pulse.

## The bug
M0 internal loopback decodes perfectly. The TWO-BOARD fiber link does NOT: link comes up
(lock=1, commadet climbing => CDR locks, commas arrive) but disperr climbs into the
thousands, byteali oscillates/0, rcv_aligned never asserts, EVT=0. Genuine 8b10b bit
errors in the received stream, no byte-align, no decode.

## Ruled out (do not re-litigate)
- Per-comma realign disruption: fixed with the "align-once LATCH".
- RX-buffer-bypass / clock-slip theory: tried RX_BUFFER_MODE=0; bb_done=1 but disperr
  still climbed. REVERTED to RX_BUFFER_MODE=1 (M0-proven). The test implicating bypass was
  itself confounded (see below), so bypass was likely never the culprit, but buffer-enabled
  is the correct baseline.
- Polarity (P/N swap): rxpolarity 0 vs 1 fail identically (an RX flip cancels any single
  inversion anywhere) -> ruled out.
- Optics module type: FTLF1318P3BTL is a proper 1.25G single-mode part; overload theory
  recomputed and dismissed.
- Fiber type: yellow jacket = single-mode, matches modules.
- Board B generator confirmed transmitting (dbg_hb on PMOD1/H12 oscillating on a scope).

## Key confound discovered
The runtime loopback SWITCH (`--gtctrl 0x08` -> loopback_in=010) is UNRELIABLE and its
results are MEANINGLESS: switching loopback_in at runtime needs the GT to relock its CDR to
the new source, but `gt_ctrl[8]` only does an RX-DATAPATH reset, not a PMA/CDR reset. So
after a runtime switch the CDR stays locked to the power-up source. Any `--gtctrl 0x08`
"internal loopback" test on M1/selftest is INCONCLUSIVE. The decode path is proven by M0
(hardwired loopback=010 from power-up), not by the runtime switch.

## Current state / next action
Latest selftest build (buffer ENABLED, reverted): md5 `5edd27a2fccc598ef832abb6db0ebe20`,
timing met WNS +4.243, on board A. PENDING CLEAN TEST (uses loopback=000, the power-up
default, no switch confound): loop ONE fiber board A SFP TX port -> board A SFP RX port,
reload bitstream (so the GT inits with the fiber present), run `--gtctrl 0x00`.
- Decodes (events, byteali=1, rcv_aligned=1, EVT climbing) -> board A SFP + fiber + RX +
  decode all good => two-board fault is board B's module or the crossover. Next: swap the
  two SFP modules / check the duplex crossover.
- disperr climbs, no decode -> board A's own SFP or that fiber is bad (NOT the FPGA/RTL).
  Also try `--gtctrl 0x01` (rxpolarity flip) on the self-loop.

## Interaction model / discipline
User runs all hardware steps and pastes board output; the assistant builds on the PC
(background Vivado), interprets [stats], decides the next experiment. Be skeptical of any
test that can be confounded (burned ~4x: polarity, align-realign, buffer-slip,
runtime-loopback-switch). Prefer the fewest-variable test. Don't claim a root cause until a
clean unconfounded test shows it. Style: NO em dashes.
