// rtl/aclk_gt_gen_bd_top.v
//
// Milestone-2 GENERATOR BD-top: the on-board aclk_gt_frame_gen drives the GT
// transceiver TX (normal mode, loopback_in=000) out the SFP+ cage, transmitting a
// real gigabit-ACLK 8b10b stream to a second KR260 running build_aclkgt_rx. No
// readout / no AXI on this board - it is a pure transmitter. The GT RX pins are
// wired (the GT is duplex) but unused.
//
// Reset: gen_rstn = 2-FF sync-deassert in tx_usrclk2, gated on rstn & tx_active.
// dbg_hb = the generator frame MARKER, divided down, to a Pmod pin (scope: is the
// generator emitting frames?).

`timescale 1ns / 1ps

module aclk_gt_gen_bd_top (
    // GT reference clock differential pair (Y6/Y5, MGTREFCLK0_224, 156.25 MHz)
    input  wire        gt_refclk_p,
    input  wire        gt_refclk_n,

    // GT serial - real SFP+ cage (TX = data out; RX wired but unused)
    output wire        gt_txp,
    output wire        gt_txn,
    input  wire        gt_rxp,
    input  wire        gt_rxn,

    // 50 MHz free-running clock for the GT reset controller (BD clk_wiz)
    input  wire        freerun_50,
    // PL reset (active-low, peripheral_aresetn, pl_clk0 domain)
    input  wire        rstn,

    // Debug: generator activity heartbeat to a Pmod pin (scope sanity)
    output wire        dbg_hb
);

    // ---- GT refclk buffer ----
    wire gt_refclk;
    IBUFDS_GTE4 #(.REFCLK_HROW_CK_SEL(2'b00)) u_ibufds_refclk (
        .I(gt_refclk_p), .IB(gt_refclk_n), .CEB(1'b0), .O(gt_refclk), .ODIV2());

    // ---- GT transceiver (normal mode, real SFP TX) ----
    wire        tx_usrclk2;
    wire        tx_active;
    wire        tx_done, rx_done;
    wire [15:0] gen_data16;
    wire [1:0]  gen_k;

    aclkgt_gt u_gt (
        .gtwiz_userclk_tx_reset_in          (1'b0),
        .gtwiz_userclk_rx_reset_in          (1'b0),
        .gtwiz_userclk_tx_srcclk_out        (),
        .gtwiz_userclk_tx_usrclk_out        (),
        .gtwiz_userclk_tx_usrclk2_out       (tx_usrclk2),
        .gtwiz_userclk_tx_active_out        (tx_active),
        .gtwiz_userclk_rx_srcclk_out        (),
        .gtwiz_userclk_rx_usrclk_out        (),
        .gtwiz_userclk_rx_usrclk2_out       (),
        .gtwiz_userclk_rx_active_out        (),
        .gtwiz_reset_clk_freerun_in         (freerun_50),
        .gtwiz_reset_all_in                 (~rstn),
        .gtwiz_reset_tx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_tx_datapath_in         (1'b0),
        .gtwiz_reset_rx_pll_and_datapath_in (1'b0),
        .gtwiz_reset_rx_datapath_in         (1'b0),
        .gtwiz_reset_rx_cdr_stable_out      (),
        .gtwiz_reset_tx_done_out            (tx_done),
        .gtwiz_reset_rx_done_out            (rx_done),
        .gtwiz_userdata_tx_in               (gen_data16),
        .gtwiz_userdata_rx_out              (),
        .gtrefclk00_in                      (gt_refclk),
        .qpll0outclk_out                    (),
        .qpll0outrefclk_out                 (),
        .gthrxn_in                          (gt_rxn),
        .gthrxp_in                          (gt_rxp),
        .gthtxn_out                         (gt_txn),      // real SFP TX (data)
        .gthtxp_out                         (gt_txp),
        .loopback_in                        (3'b000),      // normal (no loopback)
        .tx8b10ben_in                       (1'b1),
        .rx8b10ben_in                       (1'b1),
        .rxcommadeten_in                    (1'b1),
        .rxmcommaalignen_in                 (1'b1),
        .rxpcommaalignen_in                 (1'b1),
        .txctrl0_in                         (16'b0),
        .txctrl1_in                         (16'b0),
        .txctrl2_in                         ({6'b0, gen_k}),
        .rxctrl0_out                        (),
        .rxctrl1_out                        (),
        .rxctrl2_out                        (),
        .rxctrl3_out                        (),
        .gtpowergood_out                    (),
        .rxbyteisaligned_out                (),
        .rxbyterealign_out                  (),
        .rxcommadet_out                     (),
        .rxpmaresetdone_out                 (),
        .txpmaresetdone_out                 ()
    );

    // ---- TX-domain reset (async-assert, sync-deassert; gated on rstn & tx_active) ----
    reg tx_rstn_ff1 = 1'b0, tx_rstn_ff2 = 1'b0;
    wire gen_rstn_pre = rstn & tx_active;
    always @(posedge tx_usrclk2 or negedge gen_rstn_pre) begin
        if (!gen_rstn_pre) begin tx_rstn_ff1 <= 1'b0; tx_rstn_ff2 <= 1'b0; end
        else begin tx_rstn_ff1 <= 1'b1; tx_rstn_ff2 <= tx_rstn_ff1; end
    end
    wire gen_rstn = tx_rstn_ff2;

    // ---- frame generator (TX domain) ----
    wire gen_marker;
    aclk_gt_frame_gen #(.N_EVENTS(3)) u_gen (
        .CLK1    (tx_usrclk2),
        .RESETn  (gen_rstn),
        .DATA16  (gen_data16),
        .K_OUT   (gen_k),
        .MARKER  (gen_marker)
    );

    // ---- heartbeat: divide the frame-marker rate down so a scope can see it ----
    reg [19:0] hb_div = 20'd0;
    always @(posedge tx_usrclk2 or negedge gen_rstn) begin
        if (!gen_rstn) hb_div <= 20'd0;
        else if (gen_marker) hb_div <= hb_div + 20'd1;
    end
    assign dbg_hb = hb_div[19];

endmodule
