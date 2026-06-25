# Design: hand-built GT IP + ILA on the GT RX (gigabit-ACLK self-test)

Date: 2026-06-25
Branch: `aclkgt-ila` (to be created off `aclkgt-readout`)
Status: design approved, pending implementation plan

## Goal

Rebuild the `gtwizard_ultrascale` GT IP from scratch in the Vivado Transceiver Wizard
GUI (matching the current verified config), and add an Integrated Logic Analyzer (ILA)
on the GT RX signals. Programming over JTAG then gives a live waveform of what the
receiver physically captures over the SFP fiber loop. The aim is to settle, visually,
the one ambiguity the AXI counters cannot:

- `rx_data16` is non-repeating noise with `notintbl` peppered => bad optical eye (the
  fault is the SFP / fiber / optics), OR
- `rx_data16` shows recognizable content (runs of `0xFFFF` nulls, or `0xBC`/event/data
  frame bytes) but at the wrong byte/word boundary => comma-align / bit-slip / framing
  (a logic or IP-config issue we can fix in RTL).

## Background / why this approach

- The self-test (`rtl/aclk_gt_selftest_bd_top.v`) loops the board's own SFP TX into its
  own SFP RX over a real fiber. On hardware it shows `lock=1` (when not force-reset),
  `commadet` climbing (commas arrive), but `disperr` climbing, `notintbl=1`,
  `byteali=0`, no decode. M0 internal PMA loopback decodes perfectly, so the digital
  datapath and framing are proven; the corruption enters on the real serial/optical
  path. `bufslip=0` rules out an elastic-buffer slip.
- A peer reportedly got a GT link working "on itself" on the same board, but with no
  recoverable recipe. Re-creating the GT IP with the same settings is therefore most
  likely to reproduce the same result; the value comes from pairing the rebuild with an
  ILA that shows the received symbols directly.
- The user wants the hands-on GUI flow (mirrors the EECS 151 ILA exercise: add IP in
  Vivado, wire it in VS Code), building the GT IP from scratch.

## Plan

### 1. Branch + safety net
Create branch `aclkgt-ila`. The scripted `vivado/ip/gen_aclkgt_gt.tcl` and the committed
`vivado/ip/aclkgt_gt/aclkgt_gt.xci` remain in git as a known-good fallback: if the
hand-built IP is wrong, revert that one file and rebuild.

### 2. User builds the GT IP from scratch (GUI)
Vivado 2024.2 -> Manage IP (or a fresh RTL project) on part `xck26-sfvc784-2LV-c` ->
Transceivers Wizard (`gtwizard_ultrascale`), component name **`aclkgt_gt`** (same name =
RTL instantiation unchanged). Authoritative settings (extracted from the current `.xci`):

| Setting | Value |
|---|---|
| GT type | GTH (GTHE4), Quad X0Y1, Lane X0Y6 (`GTHE4_CHANNEL_X1Y12`) |
| TX/RX line rate | 1.25 Gb/s |
| TX/RX refclk | 156.25 MHz, MGTREFCLK0 of the quad (Y6/Y5) |
| TX/RX PLL | QPLL0 (secondary QPLL disabled) |
| Encoding | 8B/10B encode (TX) + decode (RX) |
| User data width | 16-bit (+ 2-bit K), usrclk2 = 62.5 MHz |
| TX/RX outclk source | TXOUTCLKPMA / RXOUTCLKPMA |
| Clocking / reset | shared logic in core (LOCATE_*_USER_CLOCKING = CORE, LOCATE_RESET_CONTROLLER = CORE) |
| Freerun clock | 50 MHz |
| RX buffer | enabled (RX_BUFFER_MODE = 1) |
| RX equalizer | LPM (INS_LOSS_NYQ = 1) |
| TX driver mode | TX_DIFF_SWING_EMPH_MODE = CUSTOM |
| Comma | K28.5 (`0xBC`): RX_COMMA_P/M_ENABLE true, P_VAL `0101111100`, M_VAL `1010000011`, MASK `1111111111`, DOUBLE false, ALIGN_WORD 1 |

Optional ports to enable (the RTL instantiation depends on all of these; `rxctrl0..3`
are auto-exposed by 8B10B):

```
loopback_in tx8b10ben_in rx8b10ben_in rxcommadeten_in rxmcommaalignen_in
rxpcommaalignen_in rxbyteisaligned_out rxbyterealign_out rxcommadet_out
rxbufstatus_out gtpowergood_out gtwiz_reset_rx_cdr_stable_out rxpmaresetdone_out
txpmaresetdone_out rxpolarity_in txpolarity_in txdiffctrl_in txpostcursor_in
txprecursor_in
```

Generate (instantiation template + synthesis). Then a **verify step**: a small command
diffs the new `aclkgt_gt.veo` port list against the expected list, so a missed optional
port is caught before the long build, not during synthesis of the top.

### 3. User builds the ILA IP (GUI)
IP Catalog -> ILA (Integrated Logic Analyzer, not System ILA), component name
**`ila_gt`**, Sample Data Depth 4096, 8 probes:

| Probe | Signal | Width |
|---|---|---|
| probe0 | `rx_data16` | 16 |
| probe1 | `rxctrl2[1:0]` (K) | 2 |
| probe2 | `rxdisperr_w[1:0]` | 2 |
| probe3 | `rxnotintbl_w[1:0]` | 2 |
| probe4 | `rxbufstatus_w[2:0]` | 3 |
| probe5 | `rx_byteali` | 1 |
| probe6 | `rx_commadet` | 1 |
| probe7 | `rx_aligned_w` | 1 |

### 4. Wire it in code (assistant)
- Place both `.xci` into `vivado/ip/` (`aclkgt_gt` replaces the scripted output; `ila_gt`
  is new).
- `vivado/build_aclkgt_selftest.tcl`: `read_ip` + `generate_target all` for `ila_gt`.
- `rtl/aclk_gt_selftest_bd_top.v`: instantiate `ila_gt` clocked by `rx_usrclk2`, probes
  wired to the cluster above. This top is synthesis-only (never simmed in Icarus), so no
  `` `ifdef `` guard is required, but the instantiation is grouped/commented so it is
  obvious it is debug-only.
- Debug hub clock: pin `dbg_hub` to the free-running `freerun_50` (not `rx_usrclk2`), so
  JTAG access survives an unstable recovered RX clock (`lock` is observed to toggle).
- XDC: ensure the `dbg_hub`/ILA clock domain is covered by the existing async clock
  groups; add a `connect_debug_port`/clock constraint only if implementation does not
  auto-resolve the hub clock.

### 5. Build + view
- Rebuild with `hw.ps1 build -Tcl vivado\build_aclkgt_selftest.tcl -Name aclkgt_selftest`.
  With an ILA present, implementation also writes a `.ltx` probes file next to the
  `.bit`.
- Connect the KR260 USB-C (JTAG/UART) to the PC running Vivado (JTAG access confirmed
  available).
- Load the bitstream the usual PS way (`fpgautil`), then Vivado -> Open Hardware Manager
  -> Auto Connect -> the device + debug hub appear -> load the `.ltx` -> set the trigger
  (immediate, or on `rx_commadet`) -> read `rx_data16` and the status bits live.

### 6. Decision tree (what the waveform means)
- `rx_data16` random / never repeats, `notintbl` frequently set => bad optical eye =>
  the fault is the SFP / fiber / optics (confirmed visually); next steps are the optical
  experiments (attenuator for overload, swap fiber/SFP).
- `rx_data16` recognizable (runs of `0xFFFF`, or `0xBC`+event+data) but mis-positioned
  => comma-align / bit-slip / framing => a logic or IP-config issue to fix in RTL/IP.
- `bufstatus`, `disperr`, `notintbl` timing patterns corroborate either reading.

## Risks / trade-offs
- From-scratch IP build is fiddly on the ~18 optional ports. Mitigations: the exact
  settings/port list above, the `.veo` verify step, and the scripted `.xci` fallback in
  git.
- ILA viewing requires JTAG / Hardware Manager access to the board (USB-C cable + Vivado
  open), a new step on top of the headless PS deploy. Confirmed available.
- The ILA adds PL logic and a debug hub; minor area/timing cost. The design currently
  closes timing with WNS +4.571 ns, so there is ample slack.

## Out of scope
- The Transceiver Wizard "example design" / PRBS path (considered, not chosen).
- Changing GT settings to test hypotheses (this rebuild matches the verified config; a
  setting sweep can follow once the ILA shows where the corruption is).
- XVC / network-based remote ILA (JTAG cable is available, so direct JTAG is simpler).
