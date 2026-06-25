// rtl/aclk_gt_selftest_bd_top.v
//
// SELF-TEST BD-top: the M1 receiver PLUS an on-board generator on the SAME board's
// real SFP TX. Identical to aclk_gt_rx_bd_top (buffer bypass + runtime GT_CTRL +
// readout) except the GT TX carries the generator's gigabit-ACLK stream instead of
// idle. The operator loops one fiber from this board's SFP TX port back to its own
// SFP RX port: the board then receives its OWN signal over the real optics (laser ->
// fiber -> photodiode -> GTH RX). Decodes => this board's SFP + that fiber + RX are
// all good (isolates a two-board failure to the OTHER board). With GT_CTRL loopback
// (--gtctrl 0x08) it falls back to internal PMA loopback as a known-good control.
//
// Data flows: frame_gen -> GTH 8b10b TX -> SFP TX -> (external fiber loop) -> SFP RX
// -> GTH 8b10b decode + K28.5 comma align -> ACLK_RCV -> readout.
//
// RX elastic buffer ENABLED (the M0-proven config; buffer bypass broke comma alignment).
//
// Reset: ro_rstn = 2-FF sync-deassert in rx_usrclk2, gated on rstn & rx_active.
// DEBUG word (0xA0): { rx_aligned[31], byteali[30], bufslip[29], notintbl[28],
//                      disperr_cnt[27:14], commadet_cnt[13:0] }
//   - commadet_cnt climbs whenever the GT RX detects a comma -> live signal present.
//   - disperr_cnt = 8b10b disparity-error count; byteali=1 -> GT byte-aligned;
//     rx_aligned=1 -> ACLK_RCV locked.
//   - bufslip[29]  (STICKY) = RX elastic buffer over/underflowed (a real word slip). Stays
//     0 on the mesochronous self-test; set => clock/ppm slip on a two-board link.
//   - notintbl[28] (STICKY) = an 8b10b not-in-table (invalid-code) symbol was seen
//     (separates invalid-symbol errors from pure running-disparity errors).

`timescale 1ns / 1ps

module aclk_gt_selftest_bd_top (
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
    wire [7:0]  rxnotintbl_w;  // rxnotintable: 8b10b invalid-code (not-in-table) error per byte
    wire [2:0]  rxbufstatus_w; // RX elastic-buffer status (101=underflow,110=overflow => slip)
    wire        rx_commadet, rx_byteali;
    wire        align_en;      // comma-align enable: on until first aligned, then latched off
    wire [31:0] gt_ctrl;             // runtime GT static-control (readout 0xF0):
                                     //   [0]=rxpolarity [1]=txpolarity [4:2]=loopback_in [8]=rx re-init
                                     //   [13:9]=TXDIFFCTRL [18:14]=TXPOSTCURSOR [23:19]=TXPRECURSOR
                                     //   [24]=full RX PLL+datapath reset pulse (true CDR relock)
    // Runtime TX-driver sweep: hunt a TX eye the real SFP link can lock on (the failure is
    // equalizer- and power-independent -> points at the TX-into-SFP drive, which near-end PMA
    // loopback never exercises). txdiffctrl field == 0 keeps the proven default 5'h18, so
    // power-up and any run WITHOUT the sweep flags behaves exactly like the prior bitstream.
    wire [4:0] tx_diffctrl   = (gt_ctrl[13:9] == 5'd0) ? 5'h18 : gt_ctrl[13:9];
    wire [4:0] tx_postcursor = gt_ctrl[18:14];
    wire [4:0] tx_precursor  = gt_ctrl[23:19];
    wire        tx_usrclk2;          // GT TX user clock (drives the on-board generator)
    wire [15:0] gen_data16;          // generator 16-bit word -> GT TX
    wire [1:0]  gen_k;               // generator K-char flags -> GT TX

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
        .gtwiz_reset_rx_pll_and_datapath_in (gt_ctrl[24]), // runtime FULL RX relock (PLL+CDR);
                                                           // datapath-only ([8]) does not relock
                                                           // the CDR to a switched source. Opt-in
                                                           // (default 0); on the shared-QPLL self-
                                                           // test it also blips TX, then recovers.
        .gtwiz_reset_rx_datapath_in         (gt_ctrl[8]),  // runtime RX re-init (apply pol)
        .gtwiz_reset_rx_cdr_stable_out      (),
        .gtwiz_reset_tx_done_out            (tx_done),
        .gtwiz_reset_rx_done_out            (rx_done),
        .gtwiz_userdata_tx_in               (gen_data16),  // on-board generator -> real SFP TX
        .gtwiz_userdata_rx_out              (rx_data16),
        .gtrefclk00_in                      (gt_refclk),
        .qpll0outclk_out                    (),
        .qpll0outrefclk_out                 (),
        .gthrxn_in                          (gt_rxn),      // real SFP RX
        .gthrxp_in                          (gt_rxp),
        .gthtxn_out                         (gt_txn),      // SFP TX (idle)
        .gthtxp_out                         (gt_txp),
        .loopback_in                        (gt_ctrl[4:2]),// 000=normal, 010=near-end PMA loopback
        .rxpolarity_in                      (gt_ctrl[0]),  // runtime RX P/N invert (cage-swap test)
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
        .txctrl2_in                         ({6'b0, gen_k}),  // generator K-char flags
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

    // ---- RX-domain reset (async-assert, sync-deassert; gated on rstn & rx_active) ----
    reg rx_rstn_ff1 = 1'b0, rx_rstn_ff2 = 1'b0;
    wire ro_rstn_pre = rstn & rx_active;
    always @(posedge rx_usrclk2 or negedge ro_rstn_pre) begin
        if (!ro_rstn_pre) begin rx_rstn_ff1 <= 1'b0; rx_rstn_ff2 <= 1'b0; end
        else begin rx_rstn_ff1 <= 1'b1; rx_rstn_ff2 <= rx_rstn_ff1; end
    end
    wire ro_rstn = rx_rstn_ff2;

    // ---- TX-domain reset + on-board generator (drives the real SFP TX) ----
    reg tx_rstn_ff1 = 1'b0, tx_rstn_ff2 = 1'b0;
    wire gen_rstn_pre = rstn & tx_active;
    always @(posedge tx_usrclk2 or negedge gen_rstn_pre) begin
        if (!gen_rstn_pre) begin tx_rstn_ff1 <= 1'b0; tx_rstn_ff2 <= 1'b0; end
        else begin tx_rstn_ff1 <= 1'b1; tx_rstn_ff2 <= tx_rstn_ff1; end
    end
    wire gen_rstn = tx_rstn_ff2;

    aclk_gt_frame_gen #(.N_EVENTS(3)) u_gen (
        .CLK1    (tx_usrclk2),
        .RESETn  (gen_rstn),
        .DATA16  (gen_data16),
        .K_OUT   (gen_k),
        .MARKER  ()
    );

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
    //   { rx_aligned[31], byteali[30], 2'b00, disperr_cnt[27:14], commadet_cnt[13:0] }
    // commadet_cnt = comma-detect count (live signal); disperr_cnt = 8b10b disparity errors;
    // byteali=1 -> GT byte-aligned; rx_aligned=1 -> ACLK_RCV locked.
    wire [13:0] commadet_cnt, disperr_cnt;
    cdc_gray_count #(.W(14)) u_cnt_commadet (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(rx_commadet),
        .dst_clk(s_axi_aclk), .count_dst(commadet_cnt));
    wire disperr_pulse = |rxdisperr_w[1:0];
    cdc_gray_count #(.W(14)) u_cnt_disperr (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(disperr_pulse),
        .dst_clk(s_axi_aclk), .count_dst(disperr_cnt));

    // ---- error/slip STICKIES (rx_usrclk2 domain, latch-and-hold, cleared by ro_rstn) ----
    // notintbl: an 8b10b NOT-IN-TABLE (invalid code) was seen -> separates invalid-symbol
    //   errors from pure running-disparity errors (different signatures on a marginal eye).
    // bufslip: the RX elastic buffer reported over/underflow (3'b101/3'b110) -> a real word
    //   slip occurred. Stays 0 on the mesochronous self-test (shared refclk); a set bit on a
    //   two-board link is the smoking gun for a clock-correction / ppm slip.
    reg notintbl_sticky = 1'b0, bufslip_sticky = 1'b0;
    always @(posedge rx_usrclk2 or negedge ro_rstn) begin
        if (!ro_rstn) begin
            notintbl_sticky <= 1'b0;
            bufslip_sticky  <= 1'b0;
        end else begin
            if (|rxnotintbl_w[1:0])                                  notintbl_sticky <= 1'b1;
            if (rxbufstatus_w == 3'b101 || rxbufstatus_w == 3'b110)  bufslip_sticky  <= 1'b1;
        end
    end

    // sync GT-health bits into the AXI domain. [29]=bufslip sticky, [28]=notintbl sticky.
    reg ba_m=0, ba_s=0, al_m=0, al_s=0, ni_m=0, ni_s=0, bs_m=0, bs_s=0;
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            ba_m<=0; ba_s<=0; al_m<=0; al_s<=0; ni_m<=0; ni_s<=0; bs_m<=0; bs_s<=0;
        end else begin
            ba_m<=rx_byteali;       ba_s<=ba_m;
            al_m<=rx_aligned_w;     al_s<=al_m;
            ni_m<=notintbl_sticky;  ni_s<=ni_m;
            bs_m<=bufslip_sticky;   bs_s<=bs_m;
        end
    end
    wire [31:0] dbg_word = {al_s, ba_s, bs_s, ni_s, disperr_cnt, commadet_cnt};

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
        .gt_ctrl        (gt_ctrl),
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
