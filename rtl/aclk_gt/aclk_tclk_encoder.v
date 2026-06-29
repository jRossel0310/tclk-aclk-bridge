// ------------------------------------------------------------
// aclk_tclk_encoder.v
//
// Live TCLK->ACLK encoder (simmable refactor of ACLK_DATA_SOURCE).
// Receives decoded TCLK events as ports (tclk_data/tclk_davn from
// the clk_40m domain), CDCs them into clk_tx, counts per-event-code
// occurrences in an inferred 256x32 RAM, and emits the 96-bit ACLK
// frame {0xBC, EVENT[15:0], DATA[63:0], CRC8} through GEARBOX_96_TO_16.
//
// No GT IP, no BRAM IP, no LFSR, no error injection -- Icarus-simmable.
// ------------------------------------------------------------
module aclk_tclk_encoder (
    input  wire        clk_tx,     // ~62.5 MHz TX (and RX loopback) clock
    input  wire        rstn_tx,    // active-low reset, synchronous to clk_tx

    // TCLK event input (clk_40m domain, active-low strobe)
    input  wire [7:0]  tclk_data,  // event byte, valid when tclk_davn is low
    input  wire        tclk_davn,  // active-low data-available strobe

    // Output to GT TX (or directly to ACLK_RCV in sim)
    output wire [15:0] data16,
    output wire [1:0]  k_out,
    output wire        marker      // high for one cycle per DATA96_VALID
);

    // --------------------------------------------------------
    // Internal signals
    // --------------------------------------------------------

    reg  [3:0]  lfsr_adv_ctr;
    reg         lfsr_adv0, lfsr_adv1;

    reg  [79:0] aclk_packet, tclk_packet;

    wire [7:0]  ACLK_TX_CRC;
    wire        ACLK_TX_CRC_VALID;

    wire [95:0] ACLK_TX_TO_GEARBOX;
    wire        ACLK_TX_TO_GEARBOX_VALID;

    wire [15:0] ACLK_DATA_OUT;

    wire [95:0] aclk_tx_noerr;

    reg         tclk_count_store;
    reg         tclk_store_sel;
    reg         tclk_count_zero_ctr_clr;
    reg         tclk_or_null;

    reg  [7:0]  tclk_event_reg;
    reg  [7:0]  tclk_count_addr;
    reg  [7:0]  tclk_count_zero_ctr;

    reg  [31:0] tclk_count_d;
    wire [31:0] tclk_count_q;  // read port of inferred count RAM (declared below)

    (* fsm_encoding = "user_encoding" *) reg  [7:0]  crnt_st_tclk_rcv, next_st_tclk_rcv;

    // --------------------------------------------------------
    // CDC: bring slow-domain DAVn strobe into clk_tx
    //
    // tclk_davn is a level signal (active-low) from the clk_40m domain.
    // tclk_data is held stable while tclk_davn is low.
    //
    // Strategy: latch tclk_data into a clk_tx-domain holding register
    // on the first clk_tx posedge that samples tclk_davn low, using a
    // "was already low" flag to suppress repeated captures on a single
    // strobe.  Then use a toggle synchronizer to generate a single-cycle
    // pulse (DAVn_in_CLK1) 2 clk_tx cycles later, matching the FSM's
    // expectation of a one-cycle strobe.
    // --------------------------------------------------------

    // Hold tclk_data and fire a toggle on the first cycle tclk_davn is seen low.
    reg davn_seen_low;   // tracks whether we already fired for this strobe
    reg davn_toggle;
    reg [7:0] tclk_data_hold;
    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx) begin
            davn_seen_low  <= 1'b0;
            davn_toggle    <= 1'b0;
            tclk_data_hold <= 8'h00;
        end else if (!tclk_davn && !davn_seen_low) begin
            davn_seen_low  <= 1'b1;
            davn_toggle    <= ~davn_toggle;
            tclk_data_hold <= tclk_data;
        end else if (tclk_davn) begin
            davn_seen_low  <= 1'b0;
        end
    end

    // 2-FF synchronizer + edge detect on the toggle
    reg t1, t2, t3;
    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx) begin t1 <= 1'b0; t2 <= 1'b0; t3 <= 1'b0; end
        else           begin t1 <= davn_toggle; t2 <= t1; t3 <= t2; end
    end
    wire DAVn_in_CLK1 = t2 ^ t3;
    wire [7:0] TCLK_DATA_cdc = tclk_data_hold;

    // --------------------------------------------------------
    // LFSR advance counter (6-cycle cadence, drives CRC pipeline)
    // --------------------------------------------------------

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            lfsr_adv_ctr <= 4'h0;
        else if (lfsr_adv_ctr == 4'h5)
            lfsr_adv_ctr <= 4'h0;
        else
            lfsr_adv_ctr <= lfsr_adv_ctr + 1'b1;
    end

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx) begin
            lfsr_adv0 <= 1'b0;
            lfsr_adv1 <= 1'b0;
        end else if (lfsr_adv_ctr == 4'h3) begin
            lfsr_adv0 <= 1'b1;
            lfsr_adv1 <= 1'b0;
        end else if (lfsr_adv_ctr == 4'h4) begin
            lfsr_adv0 <= 1'b0;
            lfsr_adv1 <= 1'b1;
        end else begin
            lfsr_adv0 <= 1'b0;
            lfsr_adv1 <= 1'b0;
        end
    end

    // --------------------------------------------------------
    // Capture TCLK event into clk_tx domain
    // --------------------------------------------------------

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            tclk_event_reg <= 8'h00;
        else if (DAVn_in_CLK1)
            tclk_event_reg <= TCLK_DATA_cdc;
    end

    // --------------------------------------------------------
    // RAM input mux
    // --------------------------------------------------------

    always @(*) begin
        if (!tclk_store_sel) begin
            tclk_count_d    = 32'h00000000;
            tclk_count_addr = tclk_count_zero_ctr;
        end else begin
            tclk_count_d    = tclk_count_q + 1'b1;
            tclk_count_addr = tclk_event_reg;
        end
    end

    // --------------------------------------------------------
    // Zeroing counter
    // --------------------------------------------------------

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            tclk_count_zero_ctr <= 8'h00;
        else if (tclk_count_zero_ctr_clr)
            tclk_count_zero_ctr <= 8'h00;
        else
            tclk_count_zero_ctr <= tclk_count_zero_ctr + 1'b1;
    end

    // --------------------------------------------------------
    // FSM: zero RAM on reset, then wait for events and update count
    // --------------------------------------------------------

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            crnt_st_tclk_rcv <= 8'h10;  // skip zeroing sweep; RAM is initialised below
        else
            crnt_st_tclk_rcv <= next_st_tclk_rcv;
    end

    always @(*) begin
        case (crnt_st_tclk_rcv)

            8'h00: begin
                tclk_count_store        = 1'b1;
                tclk_store_sel          = 1'b0;
                tclk_count_zero_ctr_clr = 1'b1;
                next_st_tclk_rcv        = 8'h01;
            end

            8'h01: begin
                tclk_count_store        = 1'b1;
                tclk_store_sel          = 1'b0;
                tclk_count_zero_ctr_clr = 1'b0;
                next_st_tclk_rcv        =
                    (tclk_count_zero_ctr == 8'hFF) ? 8'h10 : 8'h01;
            end

            8'h10: begin
                tclk_count_store        = 1'b0;
                tclk_store_sel          = 1'b1;
                tclk_count_zero_ctr_clr = 1'b1;
                next_st_tclk_rcv        =
                    (DAVn_in_CLK1) ? 8'h11 : 8'h10;
            end

            8'h11: begin
                tclk_count_store        = 1'b0;
                tclk_store_sel          = 1'b1;
                tclk_count_zero_ctr_clr = 1'b1;
                next_st_tclk_rcv        = 8'h12;
            end

            8'h12: begin
                tclk_count_store        = 1'b0;
                tclk_store_sel          = 1'b1;
                tclk_count_zero_ctr_clr = 1'b1;
                next_st_tclk_rcv        = 8'h13;
            end

            8'h13: begin
                tclk_count_store        = 1'b1;
                tclk_store_sel          = 1'b1;
                tclk_count_zero_ctr_clr = 1'b1;
                next_st_tclk_rcv        = 8'h10;
            end

            default: begin
                tclk_count_store        = 1'b0;
                tclk_store_sel          = 1'b1;
                tclk_count_zero_ctr_clr = 1'b1;
                next_st_tclk_rcv        = 8'h00;
            end
        endcase
    end

    // --------------------------------------------------------
    // Inferred 256x32 dual-port count RAM (replaces blk_mem_gen_0)
    // --------------------------------------------------------

    reg [31:0] count_ram [0:255];
    integer ram_init_i;
    initial begin
        for (ram_init_i = 0; ram_init_i < 256; ram_init_i = ram_init_i + 1)
            count_ram[ram_init_i] = 32'h0;
    end
    reg [31:0] tclk_count_q_r;
    always @(posedge clk_tx) begin
        if (tclk_count_store) count_ram[tclk_count_addr] <= tclk_count_d;
        tclk_count_q_r <= count_ram[tclk_count_addr];
    end
    assign tclk_count_q = tclk_count_q_r;

    // --------------------------------------------------------
    // Packet generation
    // --------------------------------------------------------

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            tclk_or_null <= 1'b0;
        else if (tclk_count_store && tclk_store_sel)   // real event write only; ignore zeroing sweep
            tclk_or_null <= 1'b1;
        else if (lfsr_adv0)
            tclk_or_null <= 1'b0;
    end

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            tclk_packet <= 80'h0;
        else if (tclk_count_store)
            tclk_packet <= {8'h00, tclk_count_addr, 32'h00000000, tclk_count_d};
    end

    always @(posedge clk_tx or negedge rstn_tx) begin
        if (!rstn_tx)
            aclk_packet <= 80'h0;
        else if (lfsr_adv0)
            aclk_packet <= tclk_or_null ? tclk_packet : 80'hFFFFFFFFFFFFFFFFFFFF;
    end

    // --------------------------------------------------------
    // CRC
    // --------------------------------------------------------

    CRC8_CALC uCRC8_CALC_TX (
        .RESETn    (rstn_tx),
        .CLK       (clk_tx),
        .CALC      (lfsr_adv1),
        .DATA      ({aclk_packet, 8'h00}),
        .CRC       (ACLK_TX_CRC),
        .CRC_VALID (ACLK_TX_CRC_VALID)
    );

    assign aclk_tx_noerr = {8'hBC, aclk_packet, ACLK_TX_CRC};

    assign ACLK_TX_TO_GEARBOX = aclk_tx_noerr;   // {0xBC, aclk_packet, CRC8}, no error inject

    assign ACLK_TX_TO_GEARBOX_VALID = ACLK_TX_CRC_VALID;

    // --------------------------------------------------------
    // Gearbox
    // --------------------------------------------------------

    GEARBOX_96_TO_16 uTX_GEARBOX (
        .RESETn       (rstn_tx),
        .CLK1         (clk_tx),
        .DATA96       (ACLK_TX_TO_GEARBOX),
        .K_IN         (12'b100000000000),
        .DATA96_VALID (ACLK_TX_TO_GEARBOX_VALID),
        .DATA16       (ACLK_DATA_OUT),
        .K_OUT        (k_out)
    );

    // --------------------------------------------------------
    // Outputs
    // --------------------------------------------------------

    assign data16  = ACLK_DATA_OUT;
    assign marker  = ACLK_TX_TO_GEARBOX_VALID;

endmodule
