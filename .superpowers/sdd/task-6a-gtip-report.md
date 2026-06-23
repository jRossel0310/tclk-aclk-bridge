# Task 6a - GT Wizard IP Generation Report

**Status:** DONE - clean generation, no ERRORs  
**Date:** 2026-06-23  
**Vivado:** 2024.2 (Build 5239630)  
**Part:** xck26-sfvc784-2LV-c  
**IP:** gtwizard_ultrascale 1.7 rev 19 -> module `aclkgt_gt`

---

## (a) Final working `set_property -dict { ... }` CONFIG block

```tcl
set_property -dict [list \
    CONFIG.CHANNEL_ENABLE                    {X0Y6} \
    CONFIG.TX_LINE_RATE                      {1.25} \
    CONFIG.TX_REFCLK_FREQUENCY               {156.25} \
    CONFIG.TX_USER_DATA_WIDTH                {16} \
    CONFIG.TX_DATA_ENCODING                  {8B10B} \
    CONFIG.TX_INT_DATA_WIDTH                 {20} \
    CONFIG.RX_LINE_RATE                      {1.25} \
    CONFIG.RX_REFCLK_FREQUENCY               {156.25} \
    CONFIG.RX_USER_DATA_WIDTH                {16} \
    CONFIG.RX_DATA_DECODING                  {8B10B} \
    CONFIG.RX_INT_DATA_WIDTH                 {20} \
    CONFIG.LOCATE_TX_USER_CLOCKING           {CORE} \
    CONFIG.LOCATE_RX_USER_CLOCKING           {CORE} \
    CONFIG.LOCATE_RESET_CONTROLLER           {CORE} \
    CONFIG.FREERUN_FREQUENCY                 {50} \
    CONFIG.RX_COMMA_P_ENABLE                 {TRUE} \
    CONFIG.RX_COMMA_M_ENABLE                 {TRUE} \
    CONFIG.RX_COMMA_DOUBLE_ENABLE            {FALSE} \
    CONFIG.RX_COMMA_P_VAL                    {0101111100} \
    CONFIG.RX_COMMA_M_VAL                    {1010000011} \
    CONFIG.RX_COMMA_MASK                     {1111111111} \
    CONFIG.RX_COMMA_ALIGN_WORD               {1} \
    CONFIG.RX_SLIDE_MODE                     {OFF} \
    CONFIG.ENABLE_OPTIONAL_PORTS             {loopback_in gtpowergood_out rxbyteisaligned_out rxcommadet_out rxbyterealign_out rxpmaresetdone_out txpmaresetdone_out gtwiz_reset_rx_cdr_stable_out txctrl0_in txctrl1_in txctrl2_in rxctrl0_out rxctrl1_out rxctrl2_out rxctrl3_out} \
] [get_ips aclkgt_gt]
```

---

## (b) Full port list from `aclkgt_gt.veo`

Source: `vivado/ip/aclkgt_gt/aclkgt_gt.veo`

| Port | Direction | Width | Role |
|---|---|---|---|
| `gtwiz_userclk_tx_reset_in` | input | [0:0] | clocking helper reset (tie 0 if not needed) |
| `gtwiz_userclk_tx_srcclk_out` | output | [0:0] | raw TX src clock from BUFG_GT |
| `gtwiz_userclk_tx_usrclk_out` | output | [0:0] | TX usrclk (62.5 MHz for 16-bit 8b10b) |
| **`gtwiz_userclk_tx_usrclk2_out`** | **output** | **[0:0]** | **TX usrclk2 = user data clock (drive txusrclk2); shared-logic-in-core** |
| `gtwiz_userclk_tx_active_out` | output | [0:0] | TX usrclk active flag |
| `gtwiz_userclk_rx_reset_in` | input | [0:0] | clocking helper reset (tie 0 if not needed) |
| `gtwiz_userclk_rx_srcclk_out` | output | [0:0] | raw RX src clock from BUFG_GT |
| `gtwiz_userclk_rx_usrclk_out` | output | [0:0] | RX usrclk |
| **`gtwiz_userclk_rx_usrclk2_out`** | **output** | **[0:0]** | **RX usrclk2 = RX data clock (replaces rx_clk_buffered); shared-logic-in-core** |
| `gtwiz_userclk_rx_active_out` | output | [0:0] | RX usrclk active flag |
| `gtwiz_reset_clk_freerun_in` | input | [0:0] | **free-running clock input (50 MHz; see note c)** |
| `gtwiz_reset_all_in` | input | [0:0] | master reset (active high) |
| `gtwiz_reset_tx_pll_and_datapath_in` | input | [0:0] | tie 0 |
| `gtwiz_reset_tx_datapath_in` | input | [0:0] | tie 0 |
| `gtwiz_reset_rx_pll_and_datapath_in` | input | [0:0] | tie 0 |
| `gtwiz_reset_rx_datapath_in` | input | [0:0] | tie 0 |
| `gtwiz_reset_rx_cdr_stable_out` | output | [0:0] | CDR lock indicator |
| `gtwiz_reset_tx_done_out` | output | [0:0] | TX reset done |
| `gtwiz_reset_rx_done_out` | output | [0:0] | RX reset done |
| **`gtwiz_userdata_tx_in`** | **input** | **[15:0]** | **TX 16-bit user data** |
| **`gtwiz_userdata_rx_out`** | **output** | **[15:0]** | **RX 16-bit user data** |
| **`gtrefclk00_in`** | **input** | **[0:0]** | **MGTREFCLK0 from IBUFDS_GTE4 (Y6/Y5, 156.25 MHz)** |
| `qpll0outclk_out` | output | [0:0] | QPLL0 output clock (can leave open) |
| `qpll0outrefclk_out` | output | [0:0] | QPLL0 output refclk (can leave open) |
| **`gthrxn_in`** | **input** | **[0:0]** | **SFP RX- (T1, MGTHRXN2_224)** |
| **`gthrxp_in`** | **input** | **[0:0]** | **SFP RX+ (T2, MGTHRXP2_224)** |
| **`loopback_in`** | **input** | **[2:0]** | **loopback mode (3'b010 = near-end PMA; 3'b000 = normal)** |
| `rx8b10ben_in` | input | [0:0] | enable 8b10b decode (tie 1'b1) |
| `rxcommadeten_in` | input | [0:0] | enable comma detect (tie 1'b1) |
| `rxmcommaalignen_in` | input | [0:0] | minus comma align enable |
| `rxpcommaalignen_in` | input | [0:0] | plus comma align enable |
| `tx8b10ben_in` | input | [0:0] | enable 8b10b encode (tie 1'b1) |
| `txctrl0_in` | input | [15:0] | TX disparity control (tie 16'b0) |
| `txctrl1_in` | input | [15:0] | TX inhibit control (tie 16'b0) |
| **`txctrl2_in`** | **input** | **[7:0]** | **TX K-char flags ([1:0] = K per 16-bit word)** |
| **`gthtxn_out`** | **output** | **[0:0]** | **SFP TX- (R3, MGTHTXN2_224)** |
| **`gthtxp_out`** | **output** | **[0:0]** | **SFP TX+ (R4, MGTHTXP2_224)** |
| `gtpowergood_out` | output | [0:0] | GT power good |
| `rxbyteisaligned_out` | output | [0:0] | byte alignment achieved |
| `rxbyterealign_out` | output | [0:0] | byte realignment indicator |
| `rxcommadet_out` | output | [0:0] | comma detected |
| `rxctrl0_out` | output | [15:0] | RX disparity error flags |
| `rxctrl1_out` | output | [15:0] | RX code violation flags |
| **`rxctrl2_out`** | **output** | **[7:0]** | **RX K-char flags ([1:0] = K per 16-bit word)** |
| `rxctrl3_out` | output | [7:0] | RX comma detect per byte |
| `rxpmaresetdone_out` | output | [0:0] | RX PMA reset done |
| `txpmaresetdone_out` | output | [0:0] | TX PMA reset done |

**Bold** = ports the design must wire (userdata, usrclk2, ctrl, reset, serial, refclk, loopback, status).

Key difference vs Evan's GTY design: usrclks are now **outputs** from the IP (shared-logic-in-core), not inputs. Replace `txusrclk2_in`/`rxusrclk2_in` with wires driven by `gtwiz_userclk_tx_usrclk2_out` / `gtwiz_userclk_rx_usrclk2_out`.

---

## (c) Config compromises and notes

| Topic | Requested | Actual | Reason |
|---|---|---|---|
| `FREERUN_FREQUENCY` | 100 MHz (pl_clk0) | **50 MHz** | Vivado 2024.2 valid range is (3.125, 62.5] MHz for this IP version; 100 MHz is rejected. Design must drive `gtwiz_reset_clk_freerun_in` from a <=62.5 MHz source - e.g. pl_clk0 / 2 via BUFGCE_DIV or PS PL clock output at 50 MHz. |
| `TX_INT_DATA_WIDTH` / `RX_INT_DATA_WIDTH` | 16 | **20** | With 8b/10b encoding the GT internal width must be 20 (16 user bits + 4 running-disparity bits); 16 is rejected. This is correct and expected for 8b/10b. |
| Refclk port name | `gtrefclk0_in` or `gtrefclk11_in` (Evan used gtrefclk11) | **`gtrefclk00_in`** | IP correctly names the MGTREFCLK0 of Quad X0Y1 as `gtrefclk00_in` (refclk index 0 of quad 0 in the configured channel group). This matches the hardware (Y6/Y5 = MGTREFCLK0_224). |
| Serial port names | `gtytxp/gtyrxp` (from GTY top_module.v) | **`gthtxp_out` / `gthrxp_in`** | KR260 has GTH (GTHE4), not GTY. Correct port names are gthtxp/gthrxp. Evan's top_module.v is from a GTY-based board. |
| Usrclk source | External (Evan drives txusrclk2_in / rxusrclk2_in) | **IP output** (shared-logic-in-core) | LOCATE_TX/RX_USER_CLOCKING = CORE means the IP provides BUFG_GT and exposes usrclk2 as outputs. Design wires these outputs to its logic instead of driving them. |
| Comma config method | Listed individual CONFIG.RX_COMMA_P_ENABLE etc. | Same - worked directly | No preset needed; individual enable/value/mask keys work. RX_COMMA_MASK must be 1111111111 (all ones) for a 10-bit match. |
| QPLL | Requested QPLL0 (default for 1.25 Gbps at 156.25 MHz) | **QPLL0** (auto-selected by IP) | The 1.25 Gbps / 156.25 MHz combination uses QPLL0. Evan's 10G design used QPLL1 - the qpll1lock_out / qpll1refclksel_in ports in his top_module.v are NOT present in our IP. |

---

## (d) Commands run and success evidence

**Command:**
```powershell
& "C:\Xilinx\Vivado\2024.2\bin\vivado.bat" -mode batch `
    -source vivado\ip\gen_aclkgt_gt.tcl 2>&1 | Tee-Object vivado\ip\gen.log
```

**Iterations:**
1. First run: FAILED - `TX_INT_DATA_WIDTH=16` rejected (valid=20); `FREERUN_FREQUENCY=100` rejected (range 3.125-62.5).
2. Second run: Set `TX/RX_INT_DATA_WIDTH=20`, `FREERUN_FREQUENCY=50`. Config accepted. `generate_target` INFO messages showed both Instantiation Template and Synthesis targets generating. `.veo` not found in initial search paths.
3. Third run: Added `export_ip_user_files` call. `.veo` found at `ipscratch.ip_user_files/ip/aclkgt_gt/aclkgt_gt.veo`. **No ERRORs.**

**Success evidence from log (run 3):**
```
INFO: [IP_Flow 19-1686] Generating 'Instantiation Template' target for IP 'aclkgt_gt'...
INFO: [IP_Flow 19-1686] Generating 'Synthesis' target for IP 'aclkgt_gt'...
INFO: Exporting IP user files ...
INFO: Generated .veo: C:/Users/jacob/kria-builds/ipscratch/ipscratch.ip_user_files/ip/aclkgt_gt/aclkgt_gt.veo
INFO: gen_aclkgt_gt.tcl complete.
INFO: [Common 17-206] Exiting Vivado at Tue Jun 23 11:30:40 2026...
```
Exit code: 0 (clean).

---

## Files committed

| File | Description |
|---|---|
| `vivado/ip/gen_aclkgt_gt.tcl` | IP generation script (source of truth) |
| `vivado/ip/aclkgt_gt/aclkgt_gt.xci` | IP configuration (119 KB, JSON/XML) |
| `vivado/ip/aclkgt_gt/aclkgt_gt.veo` | Instantiation template with full port list |
| `.gitignore` | Added exclusion for `vivado/ip/**/*.xml` (1.2 MB generated synthesis XML) |

Scratch project (`~/kria-builds/ipscratch/`) is outside the repo and not committed.
