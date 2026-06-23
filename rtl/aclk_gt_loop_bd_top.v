// rtl/aclk_gt_loop_bd_top.v
//
// Block-design wrapper for the Milestone-0 single-board GT loopback test.
// Instantiates the GT transceiver IP (aclkgt_gt) in near-end-PMA loopback
// (loopback_in=3'b010), the frame generator (aclk_gt_frame_gen), and the
// GT readout top (aclk_gt_readout_top). No external RX ports - serial
// loopback is entirely internal. gt_txp/gt_txn are brought out as top
// ports so the GT can place correctly; they are tied off at the connector
// in Milestone 0.
//
// AXI4-Lite attributes mirror aclk_readout_bd_top.v exactly (AXI_ADDR_W=8).
// The PS LPD master reads decoded events from u_ro.
//
// Reset topology:
//   rstn           = peripheral_aresetn (PL clk0 domain, from proc_sys_reset)
//   gtwiz_reset_all_in = ~rstn (active-high)
//   gen_rstn       = 2-FF sync-deassert in tx_usrclk2 domain,
//                    gates on rstn & gtwiz_userclk_tx_active_out
//   ro_rstn        = 2-FF sync-deassert in rx_usrclk2 domain,
//                    gates on rstn & gtwiz_userclk_rx_active_out
//
// Clocks:
//   s_axi_aclk    = pl_clk0 (100 MHz, PS)
//   freerun_50    = clk_wiz/clk_out1 (50 MHz from pl_clk0 MMCM in the BD)
//   tx_usrclk2    = gtwiz_userclk_tx_usrclk2_out (IP output, ~62.5 MHz)
//   rx_usrclk2    = gtwiz_userclk_rx_usrclk2_out (IP output, ~62.5 MHz)

`timescale 1ns / 1ps

module aclk_gt_loop_bd_top (
    // GT reference clock differential pair (Y6/Y5 - MGTREFCLK0_224, 156.25 MHz)
    input  wire        gt_refclk_p,
    input  wire        gt_refclk_n,

    // GT serial TX (loopback mode: connected internally, brought out for placement)
    output wire        gt_txp,
    output wire        gt_txn,

    // 50 MHz free-running clock for GT reset controller (from BD clk_wiz)
    input  wire        freerun_50,

    // PL reset (active-low, peripheral_aresetn from proc_sys_reset, pl_clk0 domain)
    input  wire        rstn,

    // Debug heartbeat from the readout (connects to a Pmod pin for scope probe)
    output wire        dbg_hb,

    // AXI4-Lite slave (PS clock); interfaces inferred from the attributes below
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

    // -------------------------------------------------------------------------
    // 1. IBUFDS_GTE4: differential GT refclk buffer (dedicated GT pins, no IOSTANDARD)
    // -------------------------------------------------------------------------
    wire gt_refclk;

    IBUFDS_GTE4 #(
        .REFCLK_HROW_CK_SEL (2'b00)
    ) u_ibufds_refclk (
        .I    (gt_refclk_p),
        .IB   (gt_refclk_n),
        .CEB  (1'b0),
        .O    (gt_refclk),
        .ODIV2()
    );

    // -------------------------------------------------------------------------
    // 2. GT transceiver - aclkgt_gt
    //    near-end PMA loopback (loopback_in=3'b010)
    //    RX tied off internally; shared-logic-in-core supplies usrclk2 outputs
    // -------------------------------------------------------------------------

    // GT usrclk2 outputs (clocks frame_gen and readout)
    wire tx_usrclk2;
    wire rx_usrclk2;
    wire tx_active;
    wire rx_active;

    // GT reset done
    wire tx_done;
    wire rx_done;

    // User data / K
    wire [15:0] gen_data16;
    wire [1:0]  gen_k;

    wire [15:0] rx_data16;
    wire [7:0]  rxctrl2;

    // GT RX status (rx_usrclk2 domain): comma-detect pulse + byte-aligned level
    wire rx_commadet;
    wire rx_byteali;

    aclkgt_gt u_gt (
        // clocking helper resets (tie 0 - shared-logic-in-core handles internally)
        .gtwiz_userclk_tx_reset_in              (1'b0),
        .gtwiz_userclk_rx_reset_in              (1'b0),
        // clocking helper outputs
        .gtwiz_userclk_tx_srcclk_out            (),
        .gtwiz_userclk_tx_usrclk_out            (),
        .gtwiz_userclk_tx_usrclk2_out           (tx_usrclk2),
        .gtwiz_userclk_tx_active_out            (tx_active),
        .gtwiz_userclk_rx_srcclk_out            (),
        .gtwiz_userclk_rx_usrclk_out            (),
        .gtwiz_userclk_rx_usrclk2_out           (rx_usrclk2),
        .gtwiz_userclk_rx_active_out            (rx_active),
        // reset controller
        .gtwiz_reset_clk_freerun_in             (freerun_50),
        .gtwiz_reset_all_in                     (~rstn),
        .gtwiz_reset_tx_pll_and_datapath_in     (1'b0),
        .gtwiz_reset_tx_datapath_in             (1'b0),
        .gtwiz_reset_rx_pll_and_datapath_in     (1'b0),
        .gtwiz_reset_rx_datapath_in             (1'b0),
        .gtwiz_reset_rx_cdr_stable_out          (),
        .gtwiz_reset_tx_done_out                (tx_done),
        .gtwiz_reset_rx_done_out                (rx_done),
        // user data
        .gtwiz_userdata_tx_in                   (gen_data16),
        .gtwiz_userdata_rx_out                  (rx_data16),
        // refclk
        .gtrefclk00_in                          (gt_refclk),
        // QPLL outputs (unused)
        .qpll0outclk_out                        (),
        .qpll0outrefclk_out                     (),
        // serial I/O - PMA loopback; tie off RX inputs
        .gthrxn_in                              (1'b1),
        .gthrxp_in                              (1'b0),
        .gthtxn_out                             (gt_txn),
        .gthtxp_out                             (gt_txp),
        // loopback mode: 3'b010 = near-end PMA
        .loopback_in                            (3'b010),
        // 8b10b enable
        .tx8b10ben_in                           (1'b1),
        .rx8b10ben_in                           (1'b1),
        // comma alignment
        .rxcommadeten_in                        (1'b1),
        .rxmcommaalignen_in                     (1'b1),
        .rxpcommaalignen_in                     (1'b1),
        // TX ctrl
        .txctrl0_in                             (16'b0),
        .txctrl1_in                             (16'b0),
        .txctrl2_in                             ({6'b0, gen_k}),
        // RX ctrl outputs
        .rxctrl0_out                            (),
        .rxctrl1_out                            (),
        .rxctrl2_out                            (rxctrl2),
        .rxctrl3_out                            (),
        // status outputs (diagnostics surfaced into the DEBUG word)
        .gtpowergood_out                        (),
        .rxbyteisaligned_out                    (rx_byteali),
        .rxbyterealign_out                      (),
        .rxcommadet_out                         (rx_commadet),
        .rxpmaresetdone_out                     (),
        .txpmaresetdone_out                     ()
    );

    // -------------------------------------------------------------------------
    // 3. Reset synchronizers
    //    Async-assert, sync-deassert: hold low until rstn & active_out both high.
    //    Two independent 2-FF chains (one per GT user clock domain).
    // -------------------------------------------------------------------------

    // TX domain reset sync
    reg tx_rstn_ff1 = 1'b0;
    reg tx_rstn_ff2 = 1'b0;
    wire gen_rstn_pre = rstn & tx_active;
    always @(posedge tx_usrclk2 or negedge gen_rstn_pre) begin
        if (!gen_rstn_pre) begin
            tx_rstn_ff1 <= 1'b0;
            tx_rstn_ff2 <= 1'b0;
        end else begin
            tx_rstn_ff1 <= 1'b1;
            tx_rstn_ff2 <= tx_rstn_ff1;
        end
    end
    wire gen_rstn = tx_rstn_ff2;

    // RX domain reset sync
    reg rx_rstn_ff1 = 1'b0;
    reg rx_rstn_ff2 = 1'b0;
    wire ro_rstn_pre = rstn & rx_active;
    always @(posedge rx_usrclk2 or negedge ro_rstn_pre) begin
        if (!ro_rstn_pre) begin
            rx_rstn_ff1 <= 1'b0;
            rx_rstn_ff2 <= 1'b0;
        end else begin
            rx_rstn_ff1 <= 1'b1;
            rx_rstn_ff2 <= rx_rstn_ff1;
        end
    end
    wire ro_rstn = rx_rstn_ff2;

    // -------------------------------------------------------------------------
    // 4. Frame generator (TX domain)
    // -------------------------------------------------------------------------
    wire gen_marker;   // one pulse per emitted frame (tx_usrclk2 domain)
    aclk_gt_frame_gen #(.N_EVENTS(3)) u_gen (
        .CLK1    (tx_usrclk2),
        .RESETn  (gen_rstn),
        .DATA16  (gen_data16),
        .K_OUT   (gen_k),
        .MARKER  (gen_marker)
    );

    // -------------------------------------------------------------------------
    // 5. Link-ready (TX & RX done, used as mmcm_locked proxy for the readout)
    // -------------------------------------------------------------------------
    wire link_ready = tx_done & rx_done;

    // -------------------------------------------------------------------------
    // 5b. Bring-up DEBUG word (-> readout 0xA0), formed here and synchronized into
    //     the AXI (s_axi_aclk) domain. Localizes where the chain stalls:
    //       [14:0]  marker_dst    = frames the generator has emitted (TX alive?)
    //       [29:15] commadet_dst  = commas the GT RX has detected (loopback+8b10b ok?)
    //       [30]    byteali_s     = GT RX byte-aligned
    //       [31]    algn_s        = ACLK_RCV comma-aligned (decoder locked?)
    // -------------------------------------------------------------------------
    wire rx_aligned_w;

    wire [14:0] marker_dst;
    cdc_gray_count #(.W(15)) u_cnt_marker (
        .src_clk(tx_usrclk2), .src_rstn(gen_rstn), .incr(gen_marker),
        .dst_clk(s_axi_aclk), .count_dst(marker_dst));

    wire [14:0] commadet_dst;
    cdc_gray_count #(.W(15)) u_cnt_commadet (
        .src_clk(rx_usrclk2), .src_rstn(ro_rstn), .incr(rx_commadet),
        .dst_clk(s_axi_aclk), .count_dst(commadet_dst));

    reg byteali_m = 1'b0, byteali_s = 1'b0;
    reg algn_m = 1'b0, algn_s = 1'b0;
    always @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            byteali_m <= 1'b0; byteali_s <= 1'b0;
            algn_m    <= 1'b0; algn_s    <= 1'b0;
        end else begin
            byteali_m <= rx_byteali; byteali_s <= byteali_m;
            algn_m    <= rx_aligned_w; algn_s  <= algn_m;
        end
    end
    wire [31:0] dbg_word = {algn_s, byteali_s, commadet_dst, marker_dst};

    // -------------------------------------------------------------------------
    // 6. GT readout top (RX domain + AXI pass-through)
    // -------------------------------------------------------------------------
    aclk_gt_readout_top #(
        .ADDR_WIDTH (6),
        .AXI_ADDR_W (8)
    ) u_ro (
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
