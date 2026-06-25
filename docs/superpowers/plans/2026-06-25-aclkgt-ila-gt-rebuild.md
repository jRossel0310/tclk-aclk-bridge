# Hand-built GT IP + ILA on GT RX - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. NOTE: tasks marked **[USER-GUI]** are performed by the user in the Vivado GUI / JTAG; tasks marked **[ASSISTANT]** are code/build edits. Execute inline (this session), not via subagents (subagents cannot drive the GUI).

**Goal:** Rebuild the `gtwizard_ultrascale` GT IP from scratch in the Vivado GUI and add an ILA on the GT RX cluster, so a JTAG Hardware Manager capture shows whether the receiver sees random noise (bad eye) or a structured/mis-aligned pattern (framing/slip).

**Architecture:** The user hand-builds `aclkgt_gt` (same name/ports as today, so RTL wiring is unchanged) and a new `ila_gt` IP in the Transceiver Wizard / IP Catalog. The assistant wires `ila_gt` into `rtl/aclk_gt_selftest_bd_top.v` on `rx_usrclk2`, adds `read_ip` to the build, rebuilds, and the user reads the waveform over JTAG.

**Tech Stack:** Vivado 2024.2, gtwizard_ultrascale 1.7 (GTHE4), ILA (Integrated Logic Analyzer), KR260 (xck26), block-design build via `hw.ps1`.

## Global Constraints

- Part: `xck26-sfvc784-2LV-c`.
- GT IP component name: `aclkgt_gt` (MUST match the existing name so `rtl/aclk_gt_selftest_bd_top.v` instantiation is unchanged).
- ILA IP component name: `ila_gt`.
- Build command: `.\hw.ps1 build -Tcl vivado\build_aclkgt_selftest.tcl -Name aclkgt_selftest`.
- Fallback: the scripted `vivado/ip/gen_aclkgt_gt.tcl` + the committed `aclkgt_gt.xci` are the known-good GT IP; revert that file to undo a bad hand-built IP.
- Style: no em dashes in code comments or docs.

---

### Task 1: Branch + save the GT IP port-list reference [ASSISTANT]

**Files:**
- Create: `vivado/ip/_ref_aclkgt_gt.veo.ports` (reference port list, gitignored/scratch ok)

**Interfaces:**
- Produces: the authoritative expected port list that Task 2's hand-built IP must match.

- [ ] **Step 1: Create the branch**

```bash
cd "c:\Users\jacob\Fermilab\Summer-2026\kria-2-hardware"
git checkout -b aclkgt-ila
```

- [ ] **Step 2: Snapshot the current (post-rxbufstatus) GT IP port list as the reference**

The current working-tree `aclkgt_gt.veo` is exactly what the RTL expects (it includes `rxbufstatus_out`). Extract its port names:

```bash
grep -oE '\.[a-z0-9_]+\(' vivado/ip/aclkgt_gt/aclkgt_gt.veo | sort -u > vivado/ip/_ref_aclkgt_gt.veo.ports
wc -l vivado/ip/_ref_aclkgt_gt.veo.ports
```

- [ ] **Step 3: Verify the reference captured the load-bearing optional ports**

Run:
```bash
for p in rxbufstatus_out txdiffctrl_in txpostcursor_in txprecursor_in loopback_in rxpolarity_in rxctrl1_out rxctrl2_out rxctrl3_out rxbyteisaligned_out rxcommadet_out; do grep -q "\.$p(" vivado/ip/_ref_aclkgt_gt.veo.ports && echo "OK $p" || echo "MISSING $p"; done
```
Expected: every line prints `OK <port>`.

---

### Task 2: Build the GT IP `aclkgt_gt` from scratch [USER-GUI]

**Files:**
- Produces (in a scratch Vivado project): `aclkgt_gt.xci`, `aclkgt_gt.veo`

**Interfaces:**
- Produces: a customized `gtwizard_ultrascale` IP named `aclkgt_gt` whose `.veo` port list matches `vivado/ip/_ref_aclkgt_gt.veo.ports`.

- [ ] **Step 1: Open a Manage-IP project on the KR260 part**

In Vivado 2024.2: `File -> Project -> New -> Manage IP -> New Location`. Set the location to a fresh scratch dir (e.g. `%USERPROFILE%\kria-builds\aclkgt_ila_ip`). Part = `xck26-sfvc784-2LV-c`. Finish.

- [ ] **Step 2: Launch the Transceivers Wizard**

IP Catalog -> search "Transceivers Wizard" -> double-click `Transceivers Wizard (gtwizard_ultrascale)`. In the customization dialog, set **Component Name = `aclkgt_gt`** (exact).

- [ ] **Step 3: Basic transceiver settings**

- Transceiver configuration preset: **Start from scratch**.
- Transceiver type: **GTH (GTHE4)**.
- TX line rate: **1.25** Gb/s. RX line rate: **1.25** Gb/s.
- TX reference clock: **156.25** MHz. RX reference clock: **156.25** MHz.
- Encoding (TX): **8B/10B**. Decoding (RX): **8B/10B**.
- TX user data width: **16**. RX user data width: **16**.
- TX PLL: **QPLL0**. RX PLL: **QPLL0**. Secondary QPLL: **disabled**.
- TX outclk source: **TXOUTCLKPMA**. RX outclk source: **RXOUTCLKPMA**.

- [ ] **Step 4: Physical resources**

- Quad: **X0Y1**. Channel/Lane: **X0Y6** (`GTHE4_CHANNEL_X1Y12`).
- Reference clock source: **MGTREFCLK0** of the quad (the Y6/Y5 pair).

- [ ] **Step 5: Structural / clocking / buffer**

- RX buffer: **Use / enabled** (RX_BUFFER_MODE = 1; do NOT bypass).
- Shared logic / clocking: **Include in core** for TX user clocking, RX user clocking, and the reset controller (LOCATE_*_USER_CLOCKING = CORE, LOCATE_RESET_CONTROLLER = CORE).
- Free-running clock frequency: **50** MHz.

- [ ] **Step 6: Equalization + TX driver**

- RX equalization mode: **LPM**.
- Insertion loss at Nyquist (INS_LOSS_NYQ): **1** dB.
- TX diff-swing / emphasis mode: **CUSTOM**.

- [ ] **Step 7: Comma alignment (K28.5)**

- RX comma P enable: **true**, RX comma M enable: **true**.
- Comma P value: `0101111100`. Comma M value: `1010000011`. Comma mask: `1111111111`.
- Double comma: **false**. Align word: **1**.

- [ ] **Step 8: Optional ports**

In the Optional Ports section, tick exactly these (the RTL drives/reads all of them; `rxctrl0..3` are auto-enabled by 8B/10B):

```
loopback_in tx8b10ben_in rx8b10ben_in rxcommadeten_in rxmcommaalignen_in
rxpcommaalignen_in rxbyteisaligned_out rxbyterealign_out rxcommadet_out
rxbufstatus_out gtpowergood_out gtwiz_reset_rx_cdr_stable_out rxpmaresetdone_out
txpmaresetdone_out rxpolarity_in txpolarity_in txdiffctrl_in txpostcursor_in
txprecursor_in
```

- [ ] **Step 9: Generate**

Click OK. When prompted, **Generate** the IP (Out-of-context / Global is fine; at minimum generate the Instantiation Template + Synthesis targets). Note the output dir, e.g. `<ip-location>\aclkgt_gt\`.

- [ ] **Step 10 (VERIFY): port-list diff against the reference**

In Git Bash, point `NEWVEO` at the generated `.veo`:
```bash
NEWVEO="<ip-location>/aclkgt_gt/aclkgt_gt.veo"   # fix this path
grep -oE '\.[a-z0-9_]+\(' "$NEWVEO" | sort -u > /tmp/new_ports
diff vivado/ip/_ref_aclkgt_gt.veo.ports /tmp/new_ports && echo "PORTS MATCH" || echo "PORTS DIFFER (fix the missing/extra optional ports in the wizard)"
```
Expected: `PORTS MATCH`. If it differs, re-open the IP customization, fix the listed ports (Step 8), regenerate, repeat.

---

### Task 3: Install the hand-built GT IP into the repo + verify config [ASSISTANT]

**Files:**
- Modify: `vivado/ip/aclkgt_gt/aclkgt_gt.xci` (replaced by the hand-built one)
- Modify: `vivado/ip/aclkgt_gt/aclkgt_gt.veo`

**Interfaces:**
- Consumes: the verified hand-built IP from Task 2.
- Produces: `vivado/ip/aclkgt_gt/aclkgt_gt.xci` that the build reads.

- [ ] **Step 1: Copy the hand-built .xci and .veo over the scripted ones**

```bash
cp "<ip-location>/aclkgt_gt/aclkgt_gt.xci" vivado/ip/aclkgt_gt/aclkgt_gt.xci
cp "<ip-location>/aclkgt_gt/aclkgt_gt.veo" vivado/ip/aclkgt_gt/aclkgt_gt.veo
```

- [ ] **Step 2 (VERIFY): the key config persisted in the new .xci**

Run:
```bash
grep -nE '"(RX_EQ_MODE|RX_BUFFER_MODE|TX_LINE_RATE|RX_DATA_DECODING|TX_DIFF_SWING_EMPH_MODE|RX_COMMA_P_ENABLE|TX_PLL_TYPE)"' vivado/ip/aclkgt_gt/aclkgt_gt.xci
```
Expected: `RX_EQ_MODE=LPM`, `RX_BUFFER_MODE=1`, `TX_LINE_RATE=1.25`, `RX_DATA_DECODING=8B10B`, `TX_DIFF_SWING_EMPH_MODE=CUSTOM`, `RX_COMMA_P_ENABLE=true`, `TX_PLL_TYPE=QPLL0`.

- [ ] **Step 3: Commit the hand-built GT IP**

```bash
git add vivado/ip/aclkgt_gt/aclkgt_gt.xci vivado/ip/aclkgt_gt/aclkgt_gt.veo
git commit -m "feat(aclkgt): hand-built GT IP from Transceiver Wizard (matches verified config + rxbufstatus)"
```

---

### Task 4: Build the ILA IP `ila_gt` [USER-GUI]

**Files:**
- Produces: `ila_gt.xci` (8 probes, depth 4096)

**Interfaces:**
- Produces: an `ila_gt` IP with probe0=16b, probe1..3=2b, probe4=3b, probe5..7=1b.

- [ ] **Step 1: Launch the ILA wizard**

In the same Manage-IP project: IP Catalog -> search "ILA" -> double-click **ILA (Integrated Logic Analyzer)** (NOT System ILA). Component Name = **`ila_gt`**.

- [ ] **Step 2: General Options**

- Sample Data Depth: **4096**.
- Number of Probes: **8**.
- Leave "Number of comparators" / advanced trigger at defaults.

- [ ] **Step 3: Probe widths (Probe_Ports tab)**

| Probe | Width |
|---|---|
| PROBE0 | 16 |
| PROBE1 | 2 |
| PROBE2 | 2 |
| PROBE3 | 2 |
| PROBE4 | 3 |
| PROBE5 | 1 |
| PROBE6 | 1 |
| PROBE7 | 1 |

- [ ] **Step 4: Generate**

Click OK -> Generate (synthesis + instantiation template). Note the path `<ip-location>\ila_gt\`.

- [ ] **Step 5 (VERIFY): probe widths in the stub**

```bash
grep -E "probe[0-7]" "<ip-location>/ila_gt/ila_gt_stub.v"
```
Expected: `probe0[15:0]`, `probe1[1:0]`, `probe2[1:0]`, `probe3[1:0]`, `probe4[2:0]`, `probe5`, `probe6`, `probe7` (1-bit probes shown without a range).

---

### Task 5: Install the ILA IP + wire it into the build and RTL [ASSISTANT]

**Files:**
- Create: `vivado/ip/ila_gt/ila_gt.xci` (+ `.veo`/stub copied in)
- Modify: `vivado/build_aclkgt_selftest.tcl` (read_ip the ILA)
- Modify: `rtl/aclk_gt_selftest_bd_top.v` (instantiate `ila_gt`)

**Interfaces:**
- Consumes: `ila_gt` from Task 4; the GT RX wires already present in the BD top (`rx_data16`, `rxctrl2`, `rxdisperr_w`, `rxnotintbl_w`, `rxbufstatus_w`, `rx_byteali`, `rx_commadet`, `rx_aligned_w`, `rx_usrclk2`).
- Produces: a synthesizable design with a debug core on the GT RX cluster.

- [ ] **Step 1: Copy the ILA IP into the repo**

```bash
mkdir -p vivado/ip/ila_gt
cp "<ip-location>/ila_gt/ila_gt.xci" vivado/ip/ila_gt/ila_gt.xci
```

- [ ] **Step 2: Read the ILA IP in the build tcl**

In `vivado/build_aclkgt_selftest.tcl`, immediately after the existing GT IP block (the `read_ip $gt_xci` / `generate_target all [get_ips aclkgt_gt]` lines), add:

```tcl
# ---- ILA debug IP (GT RX cluster) ----
set ila_xci [file join $root_dir vivado ip ila_gt ila_gt.xci]
read_ip $ila_xci
generate_target all [get_ips ila_gt]
```

- [ ] **Step 3: Instantiate the ILA in the BD top**

In `rtl/aclk_gt_selftest_bd_top.v`, just before the final `endmodule`, add:

```verilog
    // ---- DEBUG ILA on the GT RX cluster (synthesis-only; viewed over JTAG) ----
    // Clocked by the recovered rx_usrclk2 so it samples the RX symbols coherently.
    // probe0=rx_data16, probe1=K, probe2=disperr, probe3=notintbl, probe4=bufstatus,
    // probe5=byteali, probe6=commadet, probe7=rcv_aligned.
    ila_gt u_ila_gt (
        .clk    (rx_usrclk2),
        .probe0 (rx_data16),
        .probe1 (rxctrl2[1:0]),
        .probe2 (rxdisperr_w[1:0]),
        .probe3 (rxnotintbl_w[1:0]),
        .probe4 (rxbufstatus_w),
        .probe5 (rx_byteali),
        .probe6 (rx_commadet),
        .probe7 (rx_aligned_w)
    );

endmodule
```
(Replace the existing final `endmodule` with the block above so there is exactly one `endmodule`.)

- [ ] **Step 4 (VERIFY): RTL references resolve**

Confirm every probed signal is declared in the file:
```bash
grep -nE "rx_data16|rxctrl2|rxdisperr_w|rxnotintbl_w|rxbufstatus_w|rx_byteali|rx_commadet|rx_aligned_w|rx_usrclk2" rtl/aclk_gt_selftest_bd_top.v | head -30
```
Expected: each name appears in a `wire`/declaration line and in the `ila_gt` instance. Exactly one `endmodule` in the file (`grep -c "endmodule" rtl/aclk_gt_selftest_bd_top.v` == 1).

- [ ] **Step 5: Commit**

```bash
git add vivado/ip/ila_gt/ila_gt.xci vivado/build_aclkgt_selftest.tcl rtl/aclk_gt_selftest_bd_top.v
git commit -m "feat(aclkgt): add ILA on the GT RX cluster (rx_data16 + status), wired on rx_usrclk2"
```

---

### Task 6: Rebuild the bitstream + confirm timing and probes file [ASSISTANT]

**Files:**
- Produces: `build/kria/aclkgt_selftest/aclkgt_selftest.runs/impl_1/uart_echo_bd_wrapper.bit.bin` and `...ila.ltx` (or `...wrapper.ltx`)

- [ ] **Step 1: Build**

```powershell
.\hw.ps1 build -Tcl vivado\build_aclkgt_selftest.tcl -Name aclkgt_selftest
```
Run in the background; the IPI flake auto-retries up to 12x.

- [ ] **Step 2 (VERIFY): timing met and bitstream produced**

```bash
grep -iE "All user specified timing constraints are met|Timing constraints are not met" build/kria/aclkgt_selftest/aclkgt_selftest.runs/impl_1/*_timing_summary_routed.rpt | head -1
ls -la build/kria/aclkgt_selftest/aclkgt_selftest.runs/impl_1/*.bit.bin
```
Expected: "All user specified timing constraints are met." and the `.bit.bin` exists. Note the MD5 printed by `hw.ps1`.

- [ ] **Step 3 (VERIFY): the .ltx debug-probes file was written**

```bash
ls -la build/kria/aclkgt_selftest/aclkgt_selftest.runs/impl_1/*.ltx
```
Expected: one `.ltx` file (the probe map Hardware Manager needs). If absent, the ILA was optimized out - re-check Task 5.

---

### Task 7: Deploy + capture the ILA over JTAG [USER-GUI]

**Files:** none (hardware run)

- [ ] **Step 1: Copy bitstream + reader to the board**

```powershell
$BOARD = "ubuntu@192.168.137.3"
$bin = "build\kria\aclkgt_selftest\aclkgt_selftest.runs\impl_1\uart_echo_bd_wrapper.bit.bin"
scp $bin deploy\aclkgt_read.py deploy\tclk_filter.py "${BOARD}:~"
```

- [ ] **Step 2: Loop the fiber and load the PL**

Loop one fiber from the SFP TX port to the SFP RX port. On the board:
```bash
md5sum ~/uart_echo_bd_wrapper.bit.bin     # match the build MD5
sudo xmutil unloadapp
sudo fpgautil -b ~/uart_echo_bd_wrapper.bit.bin -o uart_echo.dtbo
sudo python3 -u ~/aclkgt_read.py /dev/uio4 --gtctrl 0x00   # confirms lock=1 + commadet climbing
```

- [ ] **Step 3: Attach Hardware Manager over JTAG**

Connect the KR260 USB-C (JTAG) to the PC. In Vivado: `Flow -> Open Hardware Manager -> Open Target -> Auto Connect`. The device (`xck26`/`arm_dap`) appears. Right-click the device -> **Refresh Device**: the debug hub + `hw_ila_1` appear (the PL was configured by `fpgautil`; do NOT reprogram).

- [ ] **Step 4: Load the probes file**

In the Hardware window, select the device, set **Probes file** = the `.ltx` from Task 6 Step 3 (Hardware Device Properties -> Probes file -> browse). Apply.

- [ ] **Step 5 (VERIFY/CAPTURE): trigger and read**

In the ILA dashboard, click **Run Trigger Immediate** (the `>>` icon). A waveform of `probe0` (`rx_data16`) + the status probes appears. Optionally set a trigger on `probe6` (`rx_commadet == 1`) and run.

Read the result:
- `rx_data16` non-repeating noise, `probe3`/notintbl peppered => **bad optical eye** (optics fault).
- `rx_data16` shows `0xFFFF` runs or `0xBC`/event/data bytes but mis-positioned => **framing / bit-slip** (logic/IP fix).

- [ ] **Step 6 (fallback if Hardware Manager will not connect)**

If the debug hub does not enumerate (RX clock stalled the hub), pin the hub to the free-running 50 MHz: add to `constraints/kr260_aclkgt_rx.xdc`, then rebuild (Task 6):
```tcl
set_property C_CLK_INPUT_FREQ_HZ 50000000 [get_debug_cores dbg_hub]
set_property C_ENABLE_CLK_DIVIDER false   [get_debug_cores dbg_hub]
connect_debug_port dbg_hub/clk [get_nets -hier -filter {NAME =~ *freerun_50*}]
```

---

## Self-Review

- **Spec coverage:** branch+fallback (Task 1), from-scratch GT IP with the exact settings sheet + optional ports (Task 2), `.veo` verify (Task 2 Step 10), repo install + config verify (Task 3), ILA IP with the 8-probe map (Task 4), build-tcl + RTL wiring on `rx_usrclk2` (Task 5), dbg_hub free-running fallback (Task 7 Step 6), build+timing+`.ltx` (Task 6), JTAG view + decision tree (Task 7). All spec sections covered.
- **Placeholder scan:** the only intentional fill-ins are `<ip-location>` (the user's chosen scratch dir) and `$BOARD` (already known: `ubuntu@192.168.137.3`); both are explicit, not vague TODOs.
- **Type/name consistency:** probe widths in Task 4 (16/2/2/2/3/1/1/1) match the Task 5 instantiation slices (`rx_data16`, `rxctrl2[1:0]`, `rxdisperr_w[1:0]`, `rxnotintbl_w[1:0]`, `rxbufstatus_w`, three 1-bit). Component names `aclkgt_gt`/`ila_gt` consistent throughout. Signal names match `rtl/aclk_gt_selftest_bd_top.v`.
