# Gigabit-ACLK-over-SFP: handoff

Self-contained state for picking up the gigabit-ACLK-over-SFP work in a fresh context.

## STATUS (2026-06-29): SELF-LOOP WORKING + ENDURANCE-PROVEN

The optical self-loop link works end to end and is rock-solid. Endurance run on board A
(selftest bitstream, one fiber SFP TX -> own SFP RX): **2h14m, 100.000% lock uptime, 0 lock
losses, 0 recoveries, 0 CRC errors, 0% disparity activity, 84.1 BILLION events decoded @
10.4 M/s** (= 62.5 MHz / 6 words-per-frame). The gateware/decode/framing were always correct;
two things were wrong and are now fixed (below). NEXT milestone: board-to-board (should now
work - the two-board failure was the same root cause), then a live Fermilab ACLK fiber.

Working bitstream: `build/.../aclkgt_selftest/.../uart_echo_bd_wrapper.bit.bin`, md5
`6cfffbc0cb328e964a7f5dcd88620c60`. Branch `aclkgt-ila` (commits up to `f6561c1`; not merged).

## Project goal
Receive the Fermilab "gigabit ACLK" serial timing link over the KR260's SFP+ optical cage
(PL GTH transceiver), decode it, and stream events to the PS (Linux) over AXI-Lite, mirroring
the shipped TCLK and ACLK-Lite readouts (which use a 3.3V pin, NOT the SFP).

- Repo: `c:\Users\jacob\Fermilab\Summer-2026\kria-2-hardware`. Vivado 2024.2, Windows 11,
  PowerShell + Git Bash. Two KR260 boards, Finisar FTLF1318P3BTL SFP modules (1000BASE-LX,
  1310nm single-mode, 1.0625-1.25G, 10km long-reach, with DDM), single-mode (yellow) fiber.

## ROOT CAUSE #1 (the dead link): SFP TX_DISABLE was never driven
The KR260 routes the SFP sideband control pins to PL I/O, and the GT does NOT control
TX_DISABLE. The Finisar module treats TX_DISABLE high/floating as LASER OFF, so the laser was
disabled on BOTH boards -> the SFP receiver amplified its own noise (CDR locked to noise, false
commadet, random rx_data16, never byte-aligned). This explained every dead end at once:
attenuation no-op (no light), TX-drive sweep no-op (no laser to drive), both boards/modules
identical (both carriers float TX_DISABLE), M0 internal loopback always clean (bypasses the SFP).
FIX: `aclk_gt_selftest_bd_top.v` now drives `assign sfp_tx_disable = 1'b0;` and monitors
rx_los/tx_fault/mod_abs in the DEBUG word.

### SFP sideband pins (KR260 carrier, PL I/O, LVCMOS33; user-confirmed against their XDC source)
| signal | pin | note |
|---|---|---|
| sfp_tx_disable | **Y10** | active-high; drive LOW to enable the laser (SLEW SLOW DRIVE 8) |
| sfp_tx_fault | A10 | 1 = module TX fault (monitor) |
| sfp_rx_los | J12 | 1 = RX loss-of-signal / no light (monitor) - the decisive optical-presence bit |
| sfp_mod_abs | W10 | 1 = module absent (monitor) |
| SFP I2C SCL / SDA | AB11 / AC11 | PL pins (NOT on the PS I2C bus); DDM needs a PL AXI-IIC to read |

For board-to-board, the TRANSMITTING board must drive its OWN Y10 low (driving the RX board's
Y10 does nothing for the far laser). The selftest bitstream does this on any board it runs on.

## ROOT CAUSE #2 (burst-then-freeze): the align-once latch latched a startup transient
Once TX_DISABLE was fixed, the link decoded a burst then froze. Cause: the old align-once latch
disabled comma alignment after the first raw `byteali` and could never re-enable it; a startup
transient stranded it. FIX: replaced it with a self-healing RX recovery FSM in
`aclk_gt_selftest_bd_top.v`:
- **SEARCH**: comma align ON, wait for DECODER lock (`ACLK_RCV rx_aligned` = 5 consecutive good
  CRC frames), NOT first byteali. This lock criterion is what actually fixed it.
- **LOCKED**: comma align OFF (no per-comma running-disparity disruption), decode.
- **RECOVER**: on `LOSS_WINDOW` (512) consecutive byteali-low cycles -> clear the decoder/lock
  state via a NEW `dec_rstn` (split from the FIFO reset; never one-side-reset the async FIFO),
  re-enable align, optionally pulse the GT RX datapath reset (`RECOVER_GT_RESET` param, default 0
  = soft re-align), then re-search.
Params at the top of the FSM (`LOSS_WINDOW`, `RECOVER_LEN`, `RECOVER_GT_RESET`). The endurance
run logged 0 recoveries -> the eye is genuinely CLEAN; the FSM's value was the lock criterion,
not the self-heal. `aclk_gt_readout_top.sv` took a `dec_rstn` port (loop/rx tops tie it to
ro_rstn). `recover_cnt[11:8]` was added to the DEBUG word (commadet shrank to [7:0]).

## Hardware facts (KR260 SFP / GTH)
- Part `xck26-sfvc784-2LV-c`. GTH (GTHE4) bank 224, Quad X0Y1 / Lane X0Y6 (GTHE4_CHANNEL_X1Y12).
- SFP serial: TX R4/R3, RX T2/T1. Refclk MGTREFCLK0_224 on Y6/Y5 @ 156.25 MHz (free-running).
- 1.25 Gbps, 8b/10b, 16-bit user data + 2-bit K, QPLL0, K28.5 comma (0xBC). rx_usrclk2 = 62.5 MHz.
- RX elastic buffer ENABLED, RX_EQ = LPM, TX_DIFF_SWING_EMPH = CUSTOM (txdiffctrl default 0x18).
- Readout AXI base 0x8000_0000 (LPD) = `/dev/uio4`. Registers spaced 16 BYTES apart (HW quirk:
  4-byte-spaced regs read back 0).

## Architecture
- Decoder `rtl/aclk_bridge/ACLK_REV.v` (`ACLK_RCV`): GT 16b + 2b-K -> EVENT[15:0] + DATA[63:0] +
  VALID/ERROR. 96-bit frame `{0xBC, EVENT, DATA, CRC8}`, CRC8 poly 0x2F, decode gated on CRC==0.
- Generator `rtl/aclk_gt/aclk_gt_frame_gen.v` (IP-free): 3 events cycling (0x0001/0x1111..,
  0x00A5/0xAAAA.., 0x1000/0x0123..), free-running ~10.4M frames/s (62.5MHz/6), NO inter-frame gap
  -> floods the dual-clock FIFO (STATUS overflow bit set; harmless for a self-loop, but add a gap
  for a clean board-to-board stream the slow PS reader can keep up with).
- Readout `rtl/aclk_readout/aclk_readout_axi.sv` (timestamp + dual-clock async FIFO + AXI4-Lite),
  wrapper `rtl/aclk_gt/aclk_gt_readout_top.sv` (DROP_NULL=1, now with the `dec_rstn` decoder reset).
- GT IP `vivado/ip/gen_aclkgt_gt.tcl` (gtwizard_ultrascale) with STAGED set_property + get_property
  hard-fail verify (GT IP set_property FAILS SILENTLY). Exposes rxbufstatus_out + the TX-driver
  sweep ports. ILA `vivado/ip/ila_gt/ila_gt.xci` (8 probes on the GT RX cluster, JTAG).

## Build targets (all `design_name=uart_echo_bd` so the overlay/UIO node is identical)
- **selftest** `build_aclkgt_selftest.tcl` -> `aclk_gt_selftest_bd_top.v`: generator + receiver on
  ONE board over the real SFP (self-loop or, cross-connected, board-to-board). HAS the TX_DISABLE
  fix + recovery FSM + ILA + full telemetry. **This is the current working build.**
- **M0** `build_aclkgt_loop.tcl` -> `aclk_gt_loop_bd_top.v`: generator + internal PMA loopback (no
  optics). Known-good baseline.
- **M1** `build_aclkgt_rx.tcl` / **M2** `build_aclkgt_gen.tcl`: dedicated receiver / transmitter.
  NOTE: M1/M2 do NOT yet have the TX_DISABLE fix - use the selftest bitstream on both boards for
  board-to-board, or port the fix into those tops first.

## Build / deploy
```
.\hw.ps1 build -Tcl vivado\build_aclkgt_selftest.tcl -Name aclkgt_selftest
```
Output: `build/kria/aclkgt_selftest/aclkgt_selftest.runs/impl_1/uart_echo_bd_wrapper.bit.bin`.
Timing: grep `All user specified timing constraints are met` in `*_timing_summary_routed.rpt`.
Deploy: `scp` the `.bit.bin` + the deploy python to the board. On board: `md5sum`,
`sudo xmutil unloadapp`, `sudo fpgautil -b ~/...bit.bin -o uart_echo.dtbo`, then run a tool below.

## Deploy tools (all Python 3 stdlib + `tclk_filter.py`)
- `deploy/aclkgt_read.py /dev/uio4 [--gtctrl 0xNN]` - live event stream + 1 Hz stats line.
- `deploy/aclkgt_monitor.py /dev/uio4 [--interval 1 --report 60 --log run.csv]` - long endurance
  monitor; wrap-corrected totals; prints a full summary on Ctrl-C. Read-only (safe to leave).
- `deploy/aclkgt_sweep.py /dev/uio4 [--post --pre]` - sweeps the GT TX driver via GT_CTRL and
  scores each setting (a TX-eye hunt; not needed now, kept for future links).

## DEBUG word (0xA0) + GT_CTRL (0xF0)
DEBUG = `{rcv_aligned[31], byteali[30], rx_los[29], notintbl[28], disperr_cnt[27:14],
tx_fault[13], mod_abs[12], recover_cnt[11:8], commadet_cnt[7:0]}`. Counters WRAP - judge by
"climbing" vs "frozen", not a single value. Healthy link: rx_los=0, byteali=1 + rcv_aligned=1
solid, EVT climbing every second, recover NOT climbing. (notintbl can read 1 from a benign
pre-lock startup transient.)
GT_CTRL: [0]=rxpolarity [1]=txpolarity [4:2]=loopback_in [8]=RX datapath re-init pulse
[13:9]=txdiffctrl [18:14]=txpostcursor [23:19]=txprecursor [24]=full RX PLL+CDR reset (--gtreset).

## Environment gotchas (will bite you)
1. Vivado IP-Integrator / IP-gen flake: build dies at "couldn't read ...init.tcl" OR
   "[IP_Flow 19-3505] Failed to generate IP 'rst_pl0'". Cause = Windows Defender real-time
   protection locking Vivado files mid-generate. FIX (ADMIN PowerShell):
   `Set-MpPreference -DisableRealtimeMonitoring $true`; verify
   `(Get-MpComputerStatus).RealTimeProtectionEnabled` == False. A REBOOT re-enables it. The agent
   shell is non-elevated and CANNOT do this; the USER must. Often a plain retry also clears it.
2. GT IP set_property silently defaults; always staged set + read-back / netlist verify.
3. Run long builds in the controller's persistent session, not a subagent.

## Confound to remember (still true)
The runtime loopback SWITCH (`--gtctrl 0x08` -> loopback_in=010) is unreliable: `gt_ctrl[8]` only
does an RX-DATAPATH reset, not a PMA/CDR reset, so the CDR stays locked to the power-up source.
`--gtreset` (gt_ctrl[24], full PLL+CDR reset) was tried but leaves lock=0 in this shared-QPLL
design. So validate the digital path via M0 (hardwired loopback from power-up), not runtime switches.

## NEXT: board-to-board (both boards wired up)
Flash the selftest bitstream (`6cfffbc0...`) to BOTH KR260s, cross-connect the fibers (A SFP TX ->
B SFP RX, and B SFP TX -> A SFP RX), `fpgautil` each, and run `aclkgt_read.py /dev/uio4 --gtctrl
0x00` on each. Each board drives its own Y10 low, generates, and receives the OTHER board's stream
(same 3 events). Expect both to decode (rx_los=0, rcv_aligned=1, EVT climbing). This should now
work - the original two-board failure was the same disabled-laser root cause. Refinements before a
real link: add an inter-frame gap to `aclk_gt_frame_gen` (so the slow PS reader keeps up without
FIFO overflow), and optionally clear the notintbl sticky on entering LOCKED. Then: tune
TX/RX_LINE_RATE to the confirmed real ACLK rate and receive a live Fermilab fiber.

## Interaction model / discipline
User runs all hardware steps and pastes board output; the assistant builds on the PC (background
Vivado), interprets [stats], decides the next experiment. Be skeptical of confoundable tests;
measure in hardware (the DEBUG word / monitor) rather than inferring from terminal output. Don't
claim a root cause until a clean unconfounded test shows it. Style: NO em dashes.
