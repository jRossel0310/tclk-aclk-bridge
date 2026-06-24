// rtl/aclk_gt_rx_bd_top.v
//
// Milestone-1 RECEIVER BD-top: the GT transceiver in normal mode (loopback_in=000)
// receiving a real gigabit-ACLK 8b10b stream off the SFP+ cage, decoded by ACLK_RCV
// and read by the PS over AXI4-Lite. This is the M0 loopback top with the on-board
// generator removed, loopback disabled, and the real SFP RX/TX pins exposed.
//
// The GT is duplex; its TX is driven idle (D0.0) and routed to the SFP TX pins (so
// the far end can lock its CDR if desired), but carries no data. Data flows in on
// the SFP RX pins -> GTH 8b10b decode + K28.5 comma align -> ACLK_RCV -> readout.
//
// The RX elastic buffer is BYPASSED (RX_BUFFER_MODE=0): the RX user logic runs on the
// recovered clock so there is no buffer to slip on the two boards' independent-refclk
// offset (the cause of the per-frame disparity errors over real fiber). The in-core
// bypass controller phase-aligns the recovered-clock domain; bb_done/bb_error report it.
//
// Reset: ro_rstn = 2-FF sync-deassert in rx_usrclk2, gated on rstn & rx_active.
// DEBUG word (0xA0): { rx_aligned[31], byteali[30], bb_done[29], bb_error[28],
//                      disperr_cnt[27:14], commadet_cnt[13:0] }
//   - commadet_cnt climbs whenever the GT RX detects a comma -> live signal present.
//   - disperr_cnt should stay ~flat with bypass (no slip); byteali=1 -> GT byte-aligned;
//     bb_done=1/bb_error=0 -> bypass aligned; rx_aligned=1 -> ACLK_RCV locked.

`timescale 1ns / 1ps

module aclk_gt_rx_bd_top (
    // GT reference clock differential pair (Y6/Y5, MGTREFCLK0_224, 156.25 MHz)
    input  wire        gt_refclk_p,
    input  wire        gt_refclk_n,

    // GT serial - real SFP+ cage (RX = data in; TX = idle out)
    input  wire        gt_rxp,
    input  wire        gt_rxn,
    output wire        gt_txp,
    output wire        gt_txn,

    // 50 MHz free-running clock for the GT reset controller (BD clk_wiz)
    input  wire        freerun_50,

    // PL reset (active-low, peripheral_aresetn, pl_clk0 domain)
    input  wire        rstn,

    output wire        dbg_hb,

    // AXI4-Lite slave (PS clock)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        s_axi_aresetn,
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
    input  wire        s_axi_rready
);

    // ---- GT refclk buffer ----
    wire gt_refclk;
    IBUFDS_GTE4 #(.REFCLK_HROW_CK_SEL(2'b00)) u_ibufds_refclk (
        .I(gt_refclk_p), .IB(gt_refclk_n), .CEB(1'b0), .O(gt_refclk), .ODIV2());

    // ---- GT transceiver (normal mode, real SFP RX) ----
    wire        rx_usrclk2;
    wire        rx_active, tx_active;
    wire        tx_done, rx_done;
    wire [15:0] rx_data16;
    wire [7:0]  rxctrl2;
    wire [15:0] rxdisperr_w;   // rxdisperr: GT 8b10b disparity error per byte
    wire        rx_commadet, rx_byteali;
    wire        align_en;      // comma-align enable: on until first aligned, then latched off
    wire        bb_done, bb_error;   // RX buffer-bypass phase-align: done / errored

    aclkgt_gt u_gt (
        .gtwiz_userclk_tx_reset_in          (1'b0),
        .gtwiz_userclk_rx_reset_in          (1'b0),
        .gtwiz_userclk_tx_srcclk_out        (),
        .gtwiz_userclk_tx_usrclk_out        (),
        .gtwiz_userclk_tx_usrclk2_out       (),
        .gtwiz_userclk_tx_active_out        (tx_active),
        .gtwiz_userclk_rx_srcclk_out        (),
        .gtwiz_userclk_rx_usrclk_out        (),
        .gtwiz_userclk_rx_usrclk2_out       (rx_usrclk2),
        .gtwiz_userclk_rx_active_out        (rx_active),
        .gtwiz_reset_clk_freerun_in         (freerun_50),
        .gtwiz_reset_all_in                 (~rstn),
        .gtwiz_reset_tx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_tx_datapath_in         (1'b0),
        .gtwiz_reset_rx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_rx_datapath_in         (1'b0),
        .gtwiz_reset_rx_cdr_stable_out      (),
        .gtwiz_reset_tx_done_out            (tx_done),
        .gtwiz_reset_rx_done_out            (rx_done),
        .gtwiz_buffbypass_rx_reset_in       (gtwiz_buffbypass_rx_reset),
        .gtwiz_buffbypass_rx_start_user_in  (1'b0),
        .gtwiz_buffbypass_rx_done_out       (bb_done),
        .gtwiz_buffbypass_rx_error_out      (bb_error),
        .gtwiz_userdata_tx_in               (16'h0000),    // TX idle (no data on this board)
        .gtwiz_userdata_rx_out              (rx_data16),
        .gtrefclk00_in                      (gt_refclk),
        .qpll0outclk_out                    (),
        .qpll0outrefclk_out                 (),
        .gthrxn_in                          (gt_rxn),      // real SFP RX
        .gthrxp_in                          (gt_rxp),
        .gthtxn_out                         (gt_txn),      // SFP TX (idle)
        .gthtxp_out                         (gt_txp),
        .loopback_in                        (3'b000),      // normal (no loopback)
        .rxpolarity_in                      (1'b0),        // no RX invert (polarity not the cause)
        .txpolarity_in                      (1'b0),
        .tx8b10ben_in                       (1'b1),
        .rx8b10ben_in                       (1'b1),
        .rxcommadeten_in                    (1'b1),
        .rxmcommaalignen_in                 (align_en),    // align once then LATCH-hold
        .rxpcommaalignen_in                 (align_en),
        .txctrl0_in                         (16'b0),
        .txctrl1_in                         (16'b0),
        .txctrl2_in                         (8'b0),
        .rxctrl0_out                        (),
        .rxctrl1_out                        (rxdisperr_w), // rxdisperr (8b10b disparity error/byte)
        .rxctrl2_out                        (rxctrl2),
        .rxctrl3_out                        (),
        .gtpowergood_out                    (),
        .rxbyteisaligned_out                (rx_byteali),
        .rxbyterealign_out                  (),
        .rxcommadet_out                     (rx_commadet),
        .rxpmaresetdone_out                 (),
        .txpmaresetdone_out                 ()
    );

    // ---- RX buffer-bypass controller reset (freerun domain) ----
    // The RX elastic buffer is bypassed (RX_BUFFER_MODE=0), so the RX user logic runs on
    // the recovered clock - no buffer to slip on the two boards' clock offset. The in-core
    // bypass controller phase-aligns the recovered-clock domain. Hold it in reset until the
    // RX user clock is active (RXUSRCLK2 stable), then release so it runs the procedure once.
    reg bb_rstn_ff1 = 1'b0, bb_rstn_ff2 = 1'b0;
    always @(posedge freerun_50 or negedge rstn) begin
        if (!rstn) begin bb_rstn_ff1 <= 1'b0; bb_rstn_ff2 <= 1'b0; end
        else begin bb_rstn_ff1 <= rx_active; bb_rstn_ff2 <= bb_rstn_ff1; end
    end
    wire gtwiz_buffbypass_rx_reset = ~bb_rstn_ff2;

    // ---- RX-domain reset (async-assert, sync-deassert; gated on rstn & rx_active) ----
    reg rx_rstn_ff1 = 1'b0, rx_rstn_ff2 = 1'b0;
    wire ro_rstn_pre = rstn & rx_active;
    always @(posedge rx_usrclk2 or negedge ro_rstn_pre) begin
        if (!ro_rstn_pre) begin rx_rstn_ff1 <= 1'b0; rx_rstn_ff2 <= 1'b0; end
        else begin rx_rstn_ff1 <= 1'b1; rx_rstn_ff2 <= rx_rstn_ff1; end
    end
    wire ro_rstn = rx_rstn_ff2;

    // Align-once latch: enable comma alignment until the first rxbyteisaligned, then
    // latch it off and HOLD. Holding the align enables high continuously makes the GT
    // re-attempt alignment on every comma; over a real (jittery) fiber each re-align
    // disrupts the 8b10b running disparity (one disparity error per frame). A plain
    // combinational ~rxbyteisaligned oscillates, so the aligned state is latched.
    reg aligned_latch = 1'b0;
    always @(posedge rx_usrclk2 or negedge ro_rstn) begin
        if (!ro_rstn)        aligned_latch <= 1'b0;
        else if (rx_byteali) aligned_latch <= 1'b1;
    end
    assign align_en = ~aligned_latch;

    wire rx_aligned_w;
    wire link_ready = tx_done & rx_done;

    // ---- GT-health DEBUG word (-> readout 0xA0), synchronized into the AXI domain ----
    //   { rx_aligned[31], byteali[30], bb_done[29], bb_error[28],
    //     disperr_cnt[27:14], commadet_cnt[13:0] }
    // With RX buffer bypass the RX runs on the recovered clock, so the elastic-buffer slip
    // is gone: disperr_cnt should now stay ~flat, byteali hold, rx_aligned assert. bb_done=1
    // / bb_error=0 confirms the bypass phase-align procedure completed (if bb_error=1 it
    // failed and the data domain never aligned).
    wire [13:0] commadet_cnt, disperr_cnt;
    cdc_gray_count #(.W(14)) u_cnt_commadet (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(rx_commadet),
        .dst_clk(s_axi_aclk), .count_dst(commadet_cnt));
    wire disperr_pulse = |rxdisperr_w[1:0];
    cdc_gray_count #(.W(14)) u_cnt_disperr (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(disperr_pulse),
        .dst_clk(s_axi_aclk), .count_dst(disperr_cnt));

    // sync GT-health bits into the AXI domain (bb_done/bb_error are slow/static)
    reg ba_m=0, ba_s=0, al_m=0, al_s=0, bbd_m=0, bbd_s=0, bbe_m=0, bbe_s=0;
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            ba_m<=0; ba_s<=0; al_m<=0; al_s<=0; bbd_m<=0; bbd_s<=0; bbe_m<=0; bbe_s<=0;
        end else begin
            ba_m<=rx_byteali;   ba_s<=ba_m;
            al_m<=rx_aligned_w; al_s<=al_m;
            bbd_m<=bb_done;     bbd_s<=bbd_m;
            bbe_m<=bb_error;    bbe_s<=bbe_m;
        end
    end
    wire [31:0] dbg_word = {al_s, ba_s, bbd_s, bbe_s, disperr_cnt, commadet_cnt};

    // ---- readout (RX domain + AXI) ----
    aclk_gt_readout_top #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) u_ro (
        .rx_clk         (rx_usrclk2),
        .rx_rstn        (ro_rstn),
        .pps            (1'b0),
        .data_from_xcvr (rx_data16),
        .k_from_xcvr    (rxctrl2[1:0]),
        .mmcm_locked    (link_ready),
        .dbg_word_in    (dbg_word),
        .rx_aligned     (rx_aligned_w),
        .dbg_event_valid(),
        .dbg_hb         (dbg_hb),
        .dropped_null   (),
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
        .s_axi_rready  (s_axi_rready)
    );

endmodule
