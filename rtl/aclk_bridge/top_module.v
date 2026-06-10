module top_module (
    // Global
    
    // TCLK side
    input  wire        TCLK,

    // GT Reference Clock (will be 156.25 MHz after 8A34001 is programmed)
    input wire gtrefclk_p,   // 8A34001_Q11_OUT_C_P
    input wire gtrefclk_n   // 8A34001_Q11_OUT_C_N
);
    wire gty_txp;
    wire gty_txn;
    wire gt_ready;
    wire ACLK;        // 60 MHz (CLK1)
    wire DEBUG_CLK;
    wire clk_rst;
    wire ps_rst;
    // ------------------------------------------------------------
    // Wires between ACLK_DATA_SOURCE and GT
    // ------------------------------------------------------------
    wire [15:0] aclk_tx_data;
    wire [1:0]  aclk_tx_k;
    wire        aclk_tx_marker;
    wire [3:0]  aclk_diag;
    // RX wires
    wire [15:0] aclk_rx_from_xcvr;
    wire [7:0]  aclk_rx_k_8bit;     // CHANGED: 8-bit instead of 16-bit
    wire [1:0]  aclk_rx_k;          // Extract just the K-char bits
    wire rx_out_clk;
    // Extract K-character bits (only lower 2 bits are used for 16-bit data)
    assign aclk_rx_k = aclk_rx_k_8bit[1:0];
    // GT ready status
    wire tx_reset_done;
    wire rx_reset_done;
    assign gt_ready = tx_reset_done & rx_reset_done;
    wire gt_refclk_to_wizard;
    wire RESETn;
    wire gt_refclk_div2; 
    reg [15:0] refclk_counter;
    wire gt_refclk_div2_bufg;
    wire [1:0] ACLK_K_TO_XCVR;
    wire [15:0] ACLK_TX_TO_XCVR;
    wire rx_clk_buffered;
    IBUFDS_GTE4 #(
        .REFCLK_EN_TX_PATH  (1'b0),
        .REFCLK_HROW_CK_SEL (2'b01), // 2'b01 maps ODIV2 to O divided by 2
        .REFCLK_ICNTL_RX    (2'b00)
    ) ibufds_inst (
        .I     (gtrefclk_p),
        .IB    (gtrefclk_n),
        .CEB   (1'b0),
        .O     (gt_refclk_to_wizard), // This goes to the GT Wizard
        .ODIV2 (gt_refclk_div2)       // This goes to the BUFG_GT for probing
    );
    
    BUFG_GT bufg_refclk (
        .I       (gt_refclk_div2),
        .O       (gt_refclk_div2_bufg),
        .CE      (1'b1),
        .CEMASK  (1'b0),
        .CLR     (1'b0),
        .CLRMASK (1'b0),
        .DIV     (3'b000) // Divide by 1 (ODIV2 is already 156.25/2 = 78.125 MHz)
    );

    always @(posedge gt_refclk_div2_bufg) begin
        refclk_counter <= refclk_counter + 1'b1;
    end

    design_1_wrapper design_1_inst(
        .ACLK(ACLK),
        .DEBUG_CLK(DEBUG_CLK),
        .ACLK_K_TO_XCVR(ACLK_K_TO_XCVR),
        .ACLK_TX_TO_XCVR(ACLK_TX_TO_XCVR),
        .RSTn(RESETn),
        .clk_rst(clk_rst),
        .ps_rst(ps_rst)
    );
    

reg [15:0] debug_counter;
wire qpll1lock;
wire gtpowergood;
wire rxpmaresetdone;
wire txpmaresetdone;
wire rx_cdr_stable;
always @(posedge DEBUG_CLK) begin
    debug_counter <= debug_counter + 1'b1;
end

gtwizard_ultrascale_0 u_gt_tx (
    // ============================================================
    // USER CLOCKS - Must be active before reset deasserts
    // ============================================================
    .gtwiz_userclk_tx_active_in       (1'b1),
    .gtwiz_userclk_rx_active_in       (rxpmaresetdone),
    
    // ============================================================
    // RESET & INITIALIZATION
    // ============================================================
    .gtwiz_reset_clk_freerun_in       (ACLK),           // Free-running clock for reset logic
    .gtwiz_reset_all_in               (~RESETn),        // Master reset (active high)
    .gtwiz_reset_tx_pll_and_datapath_in (1'b0),         // Additional TX PLL+datapath reset
    .gtwiz_reset_tx_datapath_in       (1'b0),           // Additional TX datapath reset
    .gtwiz_reset_rx_pll_and_datapath_in (1'b0),         // Additional RX PLL+datapath reset
    .gtwiz_reset_rx_datapath_in       (1'b0),           // Additional RX datapath reset
    
    .gtwiz_reset_rx_cdr_stable_out    (rx_cdr_stable),               // RX CDR stable indicator
    .gtwiz_reset_tx_done_out          (tx_reset_done),  // TX reset done
    .gtwiz_reset_rx_done_out          (rx_reset_done),  // RX reset done
    
    // ============================================================
    // REFERENCE CLOCK
    // ============================================================
    .gtrefclk11_in                    (gt_refclk_to_wizard),   // From IBUFDS_GTE4
   // .gtrefclk01_in                    (gt_refclk_to_wizard),
    // ============================================================
    // QPLL OUTPUTS (can leave open if not using external QPLL)
    // ============================================================
    .qpll1outclk_out                  (),
    .qpll1outrefclk_out               (),
    
    // ============================================================
    // USER CLOCKS (you provide these)
    // ============================================================
    .txusrclk_in                      (ACLK),
    .txusrclk2_in                     (ACLK),
    .rxusrclk_in                      (rx_clk_buffered),
    .rxusrclk2_in                     (rx_clk_buffered),
    
    // ============================================================
    // TX DATAPATH
    // ============================================================
    .gtwiz_userdata_tx_in             (ACLK_TX_TO_XCVR),   // 16-bit TX data
    .tx8b10ben_in                     (1'b1),           // Enable 8b/10b encoding
    .txctrl0_in                       (16'b0),          // TX control 0
    .txctrl1_in                       (16'b0),          // TX control 1
    .txctrl2_in                       ({6'b0, ACLK_K_TO_XCVR}), // 8-bit! K-char control
    
    // ============================================================
    // RX DATAPATH
    // ============================================================
    .gtwiz_userdata_rx_out            (aclk_rx_from_xcvr), // 16-bit RX data
    .rx8b10ben_in                     (1'b1),           // Enable 8b/10b decoding
    .rxcommadeten_in                  (1'b1),           // Enable comma detection
    .rxmcommaalignen_in               (1'b1),           // Enable minus comma alignment
    .rxpcommaalignen_in               (1'b1),           // Enable plus comma alignment
    
    .rxctrl0_out                      (),               // 16-bit RX control 0
    .rxctrl1_out                      (),               // 16-bit RX control 1
    .rxctrl2_out                      (aclk_rx_k_8bit), // 8-bit! K-char flags
    .rxctrl3_out                      (),               // 8-bit RX control 3
    // ============================================================
    // ALIGNMENT STATUS
    // ============================================================
    .rxbyteisaligned_out              (rx_byte_aligned),               // Byte alignment achieved
    .rxbyterealign_out                (),               // Byte realignment occurred
    .rxcommadet_out                   (rx_comma_det),               // Comma detected
    
    // ============================================================
    // SERIAL I/O
    // ============================================================
    .gtytxp_out                       (gty_txp),
    .gtytxn_out                       (gty_txn),
    .loopback_in  (3'b010),   // Near-end PMA loopback
    .gtyrxp_in    (1'b0),     // Still tied off (not used in internal loopback)
    .gtyrxn_in    (1'b1),
    
    // ============================================================
    // STATUS & CLOCKS
    // ============================================================
    .gtpowergood_out                  (gtpowergood),               // GT power good
    .rxoutclk_out                     (rx_out_clk),               // RX recovered clock
    .txoutclk_out                     (),               // TX output clock
    .rxpmaresetdone_out               (rxpmaresetdone),               // RX PMA reset done
    .txpmaresetdone_out               (txpmaresetdone),                // TX PMA reset done
    .qpll1locken_in (1'b1),
    .qpll1lock_out(qpll1lock),
    .qpll1refclksel_in(3'b010)
);




BUFG_GT u_rxoutclk_buf (
    .I       (rx_out_clk),
    .O       (rx_clk_buffered),
    .CE      (1'b1),
    .CEMASK  (1'b0),
    .CLR     (1'b0),
    .CLRMASK (1'b0),
    .DIV     (3'b000)
);

ila_0 u_ila (
    .clk     (ACLK),
    .probe0  (ACLK_TX_TO_XCVR),        // 16-bit TX data sent
    .probe1  (ACLK_K_TO_XCVR),            // TX K flags
    .probe2  (gt_ready),              // GT lock status
    .probe3 (tx_reset_done),
    .probe4 (rx_reset_done),
    .probe5 (RESETn),
    .probe6 (refclk_counter),
    .probe7 (gtpowergood),
    .probe8(qpll1lock),
    .probe9(txpmaresetdone)
);

ila_1 u_ila_1 (
    .clk     (rx_clk_buffered),
    .probe0  (aclk_rx_from_xcvr),
    .probe1  (aclk_rx_k_8bit),
    .probe2  (rx_byte_aligned),
    .probe3  (rx_comma_det),
    .probe4  (rxpmaresetdone),
    .probe5 (rx_cdr_stable)
);

wire [15:0] aclk_event;
wire [63:0] aclk_data;
wire aclk_valid, aclk_error,  rx_aligned_out;
wire [3:0] rev_aclk_diag;

ACLK_RCV u_aclk_rcv (
    .RESETn          (RESETn),
    .CLK1            (rx_clk_buffered),  // RX domain clock!
    .DATA_FROM_XCVR  (aclk_rx_from_xcvr), 
    .K_FROM_XCVR     (aclk_rx_k),
    .ACLK_EVENT      (aclk_event),
    .ACLK_DATA       (aclk_data),
    .ACLK_VALID      (aclk_valid),
    .ACLK_ERROR      (aclk_error),
    .RX_ALIGNED_OUT  (rx_aligned_out),
    .DIAG            (rev_aclk_diag)
);

wire real_event_valid;
assign real_event_valid = aclk_valid && (aclk_event[7:0] != 8'hFF);

ila_2 u_ila_rx (
    .clk    (rx_clk_buffered),
    .probe0 (aclk_valid),      // trigger on this
    .probe1 (aclk_error),      // check this is 0
    .probe2 (aclk_event),      // 16-bit event address
    .probe3 (aclk_data),       // 64-bit count data
    .probe4 (real_event_valid)  // should be 1
);

endmodule