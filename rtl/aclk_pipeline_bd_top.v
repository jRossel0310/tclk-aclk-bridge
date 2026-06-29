// rtl/aclk_pipeline_bd_top.v
//
// INTEGRATED block-design top for the full TCLK -> ACLK pipeline on one board.
//
// Data path (single board, one SFP fiber looped TX -> RX):
//   tclk (H12, biphase-mark) -> the ONE TCLK_RCV inside tclk_readout_top (readout #1,
//   AXI bus S_AXI) -> its decoded byte (dbg_data) + active-HIGH strobe (dbg_dav) feed
//   aclk_tclk_encoder, which re-encodes the live TCLK event stream as the gigabit-ACLK
//   8b10b word -> GT TX -> SFP TX -> (external fiber loop) -> SFP RX -> GT 8b10b decode
//   + comma align -> ACLK_RCV inside aclk_gt_readout_top (readout #2, AXI bus S_AXI2) ->
//   that decoder's tap (dbg_aclk_event/_data/_valid) feeds aclk_lite_bridge ->
//   aclk_lite_encoder, mirroring the decoded-back ACLK as ACLK-Lite biphase-mark on a
//   Pmod pin (aclk_lite_out).
//
// GT block, SFP wiring, RX self-healing recovery FSM (SEARCH/LOCKED/RECOVER), per-domain
// resets, dec_rstn, and the GT-health DEBUG word are copied verbatim from
// aclk_gt_selftest_bd_top.v. The ONLY datapath change versus the selftest top is that
// gtwiz_userdata_tx_in is driven by aclk_tclk_encoder (live TCLK re-encode), not by the
// stand-alone aclk_gt_frame_gen.
//
// Design points:
//   1. ONE TCLK decoder: tclk_readout_top owns the only TCLK_RCV; the encoder is fed from
//      its dbg_data / dbg_dav (active-HIGH) outputs, with tclk_davn = ~dbg_dav (the
//      encoder strobe is active-LOW).
//   2. ONE ACLK decoder: aclk_gt_readout_top owns the only ACLK_RCV; the bridge taps its
//      dbg_aclk_event/_data/_valid debug outputs (no second ACLK_RCV).
//
// Shared timebase: global_timebase runs in s_axi_aclk (pl_clk0) and distributes one
// 64-bit tick count to both event domains (clk_40m -> ts_tclk, rx_usrclk2 -> ts_aclk),
// so TCLK and ACLK events carry a common timeline (both readouts use USE_EXT_TS=1).

`timescale 1ns / 1ps

module aclk_pipeline_bd_top (
    // ---- TCLK input (H12, LVCMOS33 biphase-mark baseband) ----
    input  wire        tclk,

    // ---- GT reference clock differential pair (MGTREFCLK0_224, 156.25 MHz) ----
    input  wire        gt_refclk_p,
    input  wire        gt_refclk_n,

    // ---- GT serial - real SFP+ cage (RX = data in; TX = re-encoded ACLK out) ----
    input  wire        gt_rxp,
    input  wire        gt_rxn,
    output wire        gt_txp,
    output wire        gt_txn,

    // ---- 50 MHz free-running clock for the GT reset controller (BD clk_wiz) ----
    input  wire        freerun_50,

    // ---- PL reset (active-low, peripheral_aresetn, pl_clk0 domain) ----
    input  wire        rstn,

    // ---- TCLK / ACLK-Lite event-domain clocks (BD clk_wiz, as in tclk_readout_bd_top) ----
    input  wire        clk_80m,           // 80 MHz serdec oversample + ACLK-Lite enc clock
    input  wire        clk_40m,           // 40 MHz TCLK deserializer + readout #1 clock

    // ---- ACLK-Lite mirror output (Pmod pin, LVCMOS33 biphase-mark) ----
    output wire        aclk_lite_out,

    // ---- optional debug pin ----
    output wire        dbg_hb,

    // ---- SFP+ sideband control/status (PL I/O on the KR260 carrier, LVCMOS33) ----
    output wire        sfp_tx_disable,   // active-high: 0 = laser ENABLED
    input  wire        sfp_tx_fault,     // 1 = module TX fault
    input  wire        sfp_rx_los,       // 1 = module RX loss-of-signal (no light)
    input  wire        sfp_mod_abs,      // 1 = module absent

    // ==== shared AXI clock/reset for BOTH AXI4-Lite slaves (PS clock) ====
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI:S_AXI2, ASSOCIATED_RESET s_axi_aresetn" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        s_axi_aresetn,

    // ==== AXI4-Lite slave #1: TCLK readout (bus S_AXI) ====
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    input  wire [7:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  wire [7:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready,

    // ==== AXI4-Lite slave #2: ACLK readout (bus S_AXI2) ====
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 AWADDR" *)
    input  wire [7:0]  s_axi2_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 AWVALID" *)
    input  wire        s_axi2_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 AWREADY" *)
    output wire        s_axi2_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 WDATA" *)
    input  wire [31:0] s_axi2_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 WSTRB" *)
    input  wire [3:0]  s_axi2_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 WVALID" *)
    input  wire        s_axi2_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 WREADY" *)
    output wire        s_axi2_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 BRESP" *)
    output wire [1:0]  s_axi2_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 BVALID" *)
    output wire        s_axi2_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 BREADY" *)
    input  wire        s_axi2_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 ARADDR" *)
    input  wire [7:0]  s_axi2_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 ARVALID" *)
    input  wire        s_axi2_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 ARREADY" *)
    output wire        s_axi2_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 RDATA" *)
    output wire [31:0] s_axi2_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 RRESP" *)
    output wire [1:0]  s_axi2_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 RVALID" *)
    output wire        s_axi2_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI2 RREADY" *)
    input  wire        s_axi2_rready
);

    // =====================================================================
    // GT refclk buffer + SFP laser enable (verbatim from selftest top)
    // =====================================================================
    wire gt_refclk;
    IBUFDS_GTE4 #(.REFCLK_HROW_CK_SEL(2'b00)) u_ibufds_refclk (
        .I(gt_refclk_p), .IB(gt_refclk_n), .CEB(1'b0), .O(gt_refclk), .ODIV2());

    // Drive TX_DISABLE low to ENABLE the laser (the GT never drives it; the module
    // treats high/floating as laser OFF).
    assign sfp_tx_disable = 1'b0;

    // =====================================================================
    // GT transceiver (normal mode, real SFP RX). TX is the re-encoded ACLK
    // stream from aclk_tclk_encoder (gen_data16/gen_k), not aclk_gt_frame_gen.
    // =====================================================================
    wire        rx_usrclk2;
    wire        tx_usrclk2;
    wire        rx_active, tx_active;
    wire        tx_done, rx_done;
    wire [15:0] rx_data16;
    wire [7:0]  rxctrl2;
    wire [15:0] rxdisperr_w;   // rxdisperr: GT 8b10b disparity error per byte
    wire [7:0]  rxnotintbl_w;  // rxnotintable: 8b10b invalid-code (not-in-table) error per byte
    wire [2:0]  rxbufstatus_w; // RX elastic-buffer status (101=underflow,110=overflow => slip)
    wire        rx_commadet, rx_byteali;
    wire        align_en;      // comma-align enable: on until first aligned, then latched off
    wire [31:0] gt_ctrl;             // runtime GT static-control (readout 0xF0)
    // Runtime TX-driver sweep: field 0 keeps the proven default 5'h18.
    wire [4:0]  tx_diffctrl   = (gt_ctrl[13:9] == 5'd0) ? 5'h18 : gt_ctrl[13:9];
    wire [4:0]  tx_postcursor = gt_ctrl[18:14];
    wire [4:0]  tx_precursor  = gt_ctrl[23:19];
    wire [15:0] gen_data16;          // encoder 16-bit word -> GT TX
    wire [1:0]  gen_k;               // encoder K-char flags -> GT TX
    wire        recover_gt_reset;    // RX-recovery FSM -> pulse GT RX datapath reset

    aclkgt_gt u_gt (
        .gtwiz_userclk_tx_reset_in          (1'b0),
        .gtwiz_userclk_rx_reset_in          (1'b0),
        .gtwiz_userclk_tx_srcclk_out        (),
        .gtwiz_userclk_tx_usrclk_out        (),
        .gtwiz_userclk_tx_usrclk2_out       (tx_usrclk2),
        .gtwiz_userclk_tx_active_out        (tx_active),
        .gtwiz_userclk_rx_srcclk_out        (),
        .gtwiz_userclk_rx_usrclk_out        (),
        .gtwiz_userclk_rx_usrclk2_out       (rx_usrclk2),
        .gtwiz_userclk_rx_active_out        (rx_active),
        .gtwiz_reset_clk_freerun_in         (freerun_50),
        .gtwiz_reset_all_in                 (~rstn),
        .gtwiz_reset_tx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_tx_datapath_in         (1'b0),
        .gtwiz_reset_rx_pll_and_datapath_in (gt_ctrl[24]), // runtime FULL RX relock (PLL+CDR)
        .gtwiz_reset_rx_datapath_in (gt_ctrl[8] | recover_gt_reset), // re-init + FSM recover
        .gtwiz_reset_rx_cdr_stable_out      (),
        .gtwiz_reset_tx_done_out            (tx_done),
        .gtwiz_reset_rx_done_out            (rx_done),
        .gtwiz_userdata_tx_in               (gen_data16),  // re-encoded TCLK->ACLK -> SFP TX
        .gtwiz_userdata_rx_out              (rx_data16),
        .gtrefclk00_in                      (gt_refclk),
        .qpll0outclk_out                    (),
        .qpll0outrefclk_out                 (),
        .gthrxn_in                          (gt_rxn),      // real SFP RX
        .gthrxp_in                          (gt_rxp),
        .gthtxn_out                         (gt_txn),      // SFP TX
        .gthtxp_out                         (gt_txp),
        .loopback_in                        (gt_ctrl[4:2]),// 000=normal, 010=near-end PMA loopback
        .rxpolarity_in                      (gt_ctrl[0]),  // runtime RX P/N invert
        .txpolarity_in                      (gt_ctrl[1]),
        .txdiffctrl_in                      (tx_diffctrl),    // TX swing sweep    (GT_CTRL[13:9])
        .txpostcursor_in                    (tx_postcursor),  // TX post-emphasis  (GT_CTRL[18:14])
        .txprecursor_in                     (tx_precursor),   // TX pre-emphasis   (GT_CTRL[23:19])
        .tx8b10ben_in                       (1'b1),
        .rx8b10ben_in                       (1'b1),
        .rxcommadeten_in                    (1'b1),
        .rxmcommaalignen_in                 (align_en),    // align once then LATCH-hold
        .rxpcommaalignen_in                 (align_en),
        .txctrl0_in                         (16'b0),
        .txctrl1_in                         (16'b0),
        .txctrl2_in                         ({6'b0, gen_k}),  // encoder K-char flags
        .rxctrl0_out                        (),
        .rxctrl1_out                        (rxdisperr_w), // rxdisperr (8b10b disparity error/byte)
        .rxctrl2_out                        (rxctrl2),
        .rxctrl3_out                        (rxnotintbl_w),// rxnotintable (8b10b invalid-code/byte)
        .gtpowergood_out                    (),
        .rxbyteisaligned_out                (rx_byteali),
        .rxbyterealign_out                  (),
        .rxcommadet_out                     (rx_commadet),
        .rxbufstatus_out                    (rxbufstatus_w),// RX elastic-buffer over/underflow
        .rxpmaresetdone_out                 (),
        .txpmaresetdone_out                 ()
    );

    // =====================================================================
    // Per-domain resets (verbatim from selftest top)
    // =====================================================================

    // ---- RX-domain reset (async-assert, sync-deassert; gated on rstn & rx_active) ----
    reg rx_rstn_ff1 = 1'b0, rx_rstn_ff2 = 1'b0;
    wire ro_rstn_pre = rstn & rx_active;
    always @(posedge rx_usrclk2 or negedge ro_rstn_pre) begin
        if (!ro_rstn_pre) begin rx_rstn_ff1 <= 1'b0; rx_rstn_ff2 <= 1'b0; end
        else begin rx_rstn_ff1 <= 1'b1; rx_rstn_ff2 <= rx_rstn_ff1; end
    end
    wire ro_rstn = rx_rstn_ff2;

    // ---- TX-domain reset (async-assert, sync-deassert; gated on rstn & tx_active) ----
    reg tx_rstn_ff1 = 1'b0, tx_rstn_ff2 = 1'b0;
    wire gen_rstn_pre = rstn & tx_active;
    always @(posedge tx_usrclk2 or negedge gen_rstn_pre) begin
        if (!gen_rstn_pre) begin tx_rstn_ff1 <= 1'b0; tx_rstn_ff2 <= 1'b0; end
        else begin tx_rstn_ff1 <= 1'b1; tx_rstn_ff2 <= tx_rstn_ff1; end
    end
    wire gen_rstn = tx_rstn_ff2;

    // =====================================================================
    // RX link-recovery FSM (rx_usrclk2) (verbatim from selftest top)
    // =====================================================================
    wire rx_aligned_w;                          // ACLK_RCV decode lock (>=5 consecutive good CRC)
    wire link_ready = tx_done & rx_done;

    localparam integer LOSS_WINDOW      = 512;  // consecutive byteali-low cycles -> RECOVER (~8 us)
    localparam integer RECOVER_LEN      = 512;  // cycles to hold the recovery reset
    localparam         RECOVER_GT_RESET = 1'b0; // soft recovery (re-align). 1 = also GT RX reset

    localparam [1:0] S_SEARCH = 2'd0, S_LOCKED = 2'd1, S_RECOVER = 2'd2;
    reg  [1:0] rstate   = S_SEARCH;
    reg  [9:0] loss_ctr = 10'd0;
    reg  [9:0] rec_ctr  = 10'd0;
    always @(posedge rx_usrclk2 or negedge ro_rstn) begin
        if (!ro_rstn) begin
            rstate <= S_SEARCH; loss_ctr <= 10'd0; rec_ctr <= 10'd0;
        end else case (rstate)
            S_SEARCH: begin
                loss_ctr <= 10'd0; rec_ctr <= 10'd0;
                if (rx_aligned_w) rstate <= S_LOCKED;
            end
            S_LOCKED: begin
                loss_ctr <= rx_byteali ? 10'd0 : (loss_ctr + 10'd1);
                if (!rx_byteali && loss_ctr >= LOSS_WINDOW[9:0]) begin
                    rstate <= S_RECOVER; rec_ctr <= 10'd0;
                end
            end
            S_RECOVER: begin
                rec_ctr <= rec_ctr + 10'd1;
                if (rec_ctr >= RECOVER_LEN[9:0]) rstate <= S_SEARCH;
            end
            default: rstate <= S_SEARCH;
        endcase
    end

    wire recover_active     = (rstate == S_RECOVER);
    assign align_en         = (rstate != S_LOCKED);  // ON in SEARCH/RECOVER, OFF in LOCKED
    assign recover_gt_reset = recover_active & RECOVER_GT_RESET;
    // local recovery reset for the decoder + lock + notintbl (NOT the async FIFO pointers).
    wire ro_rstn_eff        = ro_rstn & ~recover_active;
    wire recover_pulse      = recover_active && (rec_ctr == 10'd0);  // 1-cycle on entering RECOVER

    // =====================================================================
    // GT-health DEBUG word (-> readout 0xA0), synchronized into the AXI domain
    // (verbatim from selftest top)
    // =====================================================================
    wire [7:0]  commadet_cnt;
    wire [3:0]  recover_cnt;
    wire [13:0] disperr_cnt;
    cdc_gray_count #(.W(8)) u_cnt_commadet (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(rx_commadet),
        .dst_clk(s_axi_aclk), .count_dst(commadet_cnt));
    cdc_gray_count #(.W(4)) u_cnt_recover (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(recover_pulse),
        .dst_clk(s_axi_aclk), .count_dst(recover_cnt));
    wire disperr_pulse = |rxdisperr_w[1:0];
    cdc_gray_count #(.W(14)) u_cnt_disperr (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(disperr_pulse),
        .dst_clk(s_axi_aclk), .count_dst(disperr_cnt));

    // ---- notintbl STICKY (rx_usrclk2 domain, latch-and-hold, cleared by ro_rstn_eff) ----
    reg notintbl_sticky = 1'b0;
    always @(posedge rx_usrclk2 or negedge ro_rstn_eff) begin
        if (!ro_rstn_eff)            notintbl_sticky <= 1'b0;
        else if (|rxnotintbl_w[1:0]) notintbl_sticky <= 1'b1;
    end

    // sync GT-health + SFP sideband bits into the AXI domain.
    reg ba_m=0, ba_s=0, al_m=0, al_s=0, ni_m=0, ni_s=0;
    reg rl_m=0, rl_s=0, tf_m=0, tf_s=0, ma_m=0, ma_s=0;
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            ba_m<=0; ba_s<=0; al_m<=0; al_s<=0; ni_m<=0; ni_s<=0;
            rl_m<=0; rl_s<=0; tf_m<=0; tf_s<=0; ma_m<=0; ma_s<=0;
        end else begin
            ba_m<=rx_byteali;       ba_s<=ba_m;
            al_m<=rx_aligned_w;     al_s<=al_m;
            ni_m<=notintbl_sticky;  ni_s<=ni_m;
            rl_m<=sfp_rx_los;       rl_s<=rl_m;
            tf_m<=sfp_tx_fault;     tf_s<=tf_m;
            ma_m<=sfp_mod_abs;      ma_s<=ma_m;
        end
    end
    wire [31:0] dbg_word =
        {al_s, ba_s, rl_s, ni_s, disperr_cnt, tf_s, ma_s, recover_cnt, commadet_cnt};

    // =====================================================================
    // Shared 64-bit timebase: ref in pl_clk0, distributed to both event domains
    // =====================================================================
    wire [63:0] ts_tclk;   // clk_40m  domain (TCLK readout)
    wire [63:0] ts_aclk;   // rx_usrclk2 domain (ACLK readout)
    global_timebase u_tb (
        .ref_clk   (s_axi_aclk),
        .ref_rstn  (s_axi_aresetn),
        .dst_clk_a (clk_40m),
        .ts_a      (ts_tclk),
        .dst_clk_b (rx_usrclk2),
        .ts_b      (ts_aclk)
    );

    // =====================================================================
    // Readout #1: TCLK (owns the ONE TCLK_RCV). Its dbg_data/dbg_dav feed the
    // TCLK->ACLK encoder, so there is no second TCLK_RCV.
    // =====================================================================
    wire [7:0] tclk_dbg_data;   // decoded TCLK event byte (clk_40m)
    wire       tclk_dbg_dav;    // active-HIGH 1-cycle strobe (clk_40m)

    tclk_readout_top #(
        .ADDR_WIDTH (6),
        .AXI_ADDR_W (8),
        .USE_EXT_TS (1'b1)
    ) u_ro_tclk (
        .clk_80m       (clk_80m),
        .clk_40m       (clk_40m),
        .rstn          (rstn),
        .pps           (1'b0),
        .tclk          (tclk),
        .mmcm_locked   (1'b1),
        .ts_ext        (ts_tclk),

        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),
        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready),

        .dbg_dav       (tclk_dbg_dav),    // active-HIGH ~DAVn: one strobe per decoded byte
        .dbg_data      (tclk_dbg_data),   // decoded TCLK event byte
        .dbg_perr      (),
        .dbg_sig_err   (),
        .dbg_hb        (dbg_hb),
        .dropped_null  ()
    );

    // =====================================================================
    // TCLK -> gigabit-ACLK encoder. Fed from the ONE TCLK decoder above:
    //   tclk_data = dbg_data; tclk_davn = ~dbg_dav (encoder strobe is active-LOW).
    // Output drives the GT TX (gen_data16 / gen_k).
    // =====================================================================
    aclk_tclk_encoder u_enc (
        .clk_tx    (tx_usrclk2),
        .rstn_tx   (gen_rstn),
        .clk_40m   (clk_40m),
        .tclk_data (tclk_dbg_data),
        .tclk_davn (~tclk_dbg_dav),       // active-LOW strobe from the active-HIGH dbg_dav
        .data16    (gen_data16),
        .k_out     (gen_k),
        .marker    ()
    );

    // =====================================================================
    // Readout #2: ACLK (owns the ONE ACLK_RCV). dec_rstn from the recovery FSM.
    // Its dbg_aclk_event/_data/_valid tap the decoder for the ACLK-Lite mirror.
    // =====================================================================
    wire [15:0] aclk_event_tap;
    wire [63:0] aclk_data_tap;
    wire        aclk_valid_tap;

    aclk_gt_readout_top #(
        .ADDR_WIDTH (6),
        .AXI_ADDR_W (8),
        .USE_EXT_TS (1'b1)
    ) u_ro_aclk (
        .rx_clk         (rx_usrclk2),
        .rx_rstn        (ro_rstn),
        .dec_rstn       (ro_rstn_eff),  // recovery FSM resets the decoder, not the FIFO
        .pps            (1'b0),
        .data_from_xcvr (rx_data16),
        .k_from_xcvr    (rxctrl2[1:0]),
        .mmcm_locked    (link_ready),
        .dbg_word_in    (dbg_word),
        .ts_ext         (ts_aclk),
        .rx_aligned     (rx_aligned_w),
        .dbg_event_valid(),
        .dbg_hb         (),
        .dropped_null   (),
        .gt_ctrl        (gt_ctrl),

        .dbg_aclk_event (aclk_event_tap),
        .dbg_aclk_data  (aclk_data_tap),
        .dbg_aclk_valid (aclk_valid_tap),

        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi2_awaddr),
        .s_axi_awvalid (s_axi2_awvalid),
        .s_axi_awready (s_axi2_awready),
        .s_axi_wdata   (s_axi2_wdata),
        .s_axi_wstrb   (s_axi2_wstrb),
        .s_axi_wvalid  (s_axi2_wvalid),
        .s_axi_wready  (s_axi2_wready),
        .s_axi_bresp   (s_axi2_bresp),
        .s_axi_bvalid  (s_axi2_bvalid),
        .s_axi_bready  (s_axi2_bready),
        .s_axi_araddr  (s_axi2_araddr),
        .s_axi_arvalid (s_axi2_arvalid),
        .s_axi_arready (s_axi2_arready),
        .s_axi_rdata   (s_axi2_rdata),
        .s_axi_rresp   (s_axi2_rresp),
        .s_axi_rvalid  (s_axi2_rvalid),
        .s_axi_rready  (s_axi2_rready)
    );

    // =====================================================================
    // ACLK-Lite mirror: bridge (rx_usrclk2 -> clk_80m) + encoder (clk_80m).
    // Decoded-back ACLK real events drive the ACLK-Lite biphase-mark line.
    // =====================================================================
    wire [15:0] lite_event_id;
    wire [63:0] lite_data;
    wire [1:0]  lite_frame_type;
    wire        lite_start;
    wire        lite_busy;

    aclk_lite_bridge u_bridge (
        .rx_clk         (rx_usrclk2),
        .rx_rstn        (ro_rstn),
        .aclk_valid     (aclk_valid_tap),
        .aclk_event     (aclk_event_tap),
        .aclk_data      (aclk_data_tap),
        .enc_clk        (clk_80m),
        .enc_rstn       (rstn),
        .enc_event_id   (lite_event_id),
        .enc_data       (lite_data),
        .enc_frame_type (lite_frame_type),
        .enc_start      (lite_start),
        .enc_busy       (lite_busy),
        .dropped_count  ()
    );

    aclk_lite_encoder #(.SAMPLES_PER_CELL(8)) u_lite (
        .clk        (clk_80m),
        .rstn       (rstn),
        .start      (lite_start),
        .event_id   (lite_event_id),
        .data       (lite_data),
        .frame_type (lite_frame_type),
        .line       (aclk_lite_out),
        .busy       (lite_busy)
    );

endmodule
