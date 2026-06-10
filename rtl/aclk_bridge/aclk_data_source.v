// ------------------------------------------------------------
// ACLK_DATA_SOURCE.v
// Translated from VHDL
// ------------------------------------------------------------

module ACLK_DATA_SOURCE (
    input  wire        RESETn,
    input  wire        CLK1,
    input  wire        CLK_80MHZ, 
    input  wire        CLK_40MHZ,

    output wire [15:0] ACLK_TX_TO_XCVR,
    output wire [1:0]  ACLK_K_TO_XCVR,

    input  wire        ERROR_INPUTn,
    input  wire        TCLK,

    output wire        ACLK_TX_MARKER,
    output wire [3:0]  DIAG, 
    output wire [7:0] tclk_event_reg_debug,
    output wire TCLK_DAVn_debug,
    output wire [7:0] tclk_count_addr_debug,
    output wire [31:0] tclk_count_d_debug, 
    output wire tclk_count_store_debug,
    output wire [7:0] crnt_st_tclk_rcv_debug,
    output wire tclk_or_null_debug,
    output wire lfsr_adv0_debug,
    output wire lfsr_adv1_debug, 
    output wire [79:0] aclk_packet_debug,
    output wire [79:0] tclk_packet_debug,
    output wire aclk_tx_noerr_debug,
    output wire ACLK_TX_CRC_VALID_debug,
    output wire [15:0] ACLK_DATA_OUT_debug
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
    reg  [15:0] ACLK_DATA_OUT_bitreverse;

    wire [95:0] aclk_tx_noerr;
    wire [11:0] aclk_tx_k;

    wire        TCLK_DAVn;
    wire [7:0]  TCLK_DATA;

    reg         tclk_count_store;
    reg         tclk_store_sel;
    reg         tclk_count_zero_ctr_clr;
    reg         tclk_or_null;

    reg  [7:0]  tclk_event_reg;
    reg  [7:0]  tclk_count_addr;
    reg  [7:0]  tclk_count_zero_ctr;

    reg  [31:0] tclk_count_d;
    wire [31:0] tclk_count_q;
    
    (* fsm_encoding = "user_encoding" *) reg  [7:0]  crnt_st_tclk_rcv, next_st_tclk_rcv;

    wire [79:0] PR_PATT_80;
    wire [79:0] prpg_tx_output;
    wire [79:0] prpg_tx_biterrs;

    reg         ERROR_INPUTn_cap, ERROR_INPUTn_smpl;
    wire        ERROR_INPUTn_edge;
    
    reg DAVn_toggle;
    reg [7:0] TCLK_DATA_cdc;
    reg toggle_sync1, toggle_sync2, toggle_sync2_d;
    
    always @(posedge CLK_40MHZ or negedge RESETn) begin
        if (!RESETn) begin
            DAVn_toggle   <= 1'b0;
            TCLK_DATA_cdc <= 8'h00;
        end else if (!TCLK_DAVn) begin
            DAVn_toggle   <= ~DAVn_toggle;
            TCLK_DATA_cdc <= TCLK_DATA;
        end
    end
    
    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            toggle_sync1   <= 1'b0;
            toggle_sync2   <= 1'b0;
            toggle_sync2_d <= 1'b0;
        end else begin
            toggle_sync1   <= DAVn_toggle;
            toggle_sync2   <= toggle_sync1;
            toggle_sync2_d <= toggle_sync2;
        end
    end
    
    wire DAVn_in_CLK1 = toggle_sync2 ^ toggle_sync2_d;
    // --------------------------------------------------------
    // LFSR advance counter
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            lfsr_adv_ctr <= 4'h0;
        else if (lfsr_adv_ctr == 4'h5)
            lfsr_adv_ctr <= 4'h0;
        else
            lfsr_adv_ctr <= lfsr_adv_ctr + 1'b1;
    end

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
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
    // Capture TCLK event
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
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

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_count_zero_ctr <= 8'h00;
        else if (tclk_count_zero_ctr_clr)
            tclk_count_zero_ctr <= 8'h00;
        else
            tclk_count_zero_ctr <= tclk_count_zero_ctr + 1'b1;
    end

    // --------------------------------------------------------
    // FSM
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            crnt_st_tclk_rcv <= 8'h00;
        else
            crnt_st_tclk_rcv <= next_st_tclk_rcv;
    end

    always @(*) begin
        case (crnt_st_tclk_rcv)

            8'h00: begin
                tclk_count_store           = 1'b1;
                tclk_store_sel             = 1'b0;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h01;
            end

            8'h01: begin
                tclk_count_store           = 1'b1;
                tclk_store_sel             = 1'b0;
                tclk_count_zero_ctr_clr    = 1'b0;
                next_st_tclk_rcv           =
                    (tclk_count_zero_ctr == 8'hFF) ? 8'h10 : 8'h01;
            end

            8'h10: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           =
                    (DAVn_in_CLK1) ? 8'h11 : 8'h10;
            end

            8'h11: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h12;
            end

            8'h12: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h13;
            end

            8'h13: begin
                tclk_count_store           = 1'b1;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h10;
            end

            default: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h00;
            end
        endcase
    end

    // --------------------------------------------------------
    // Packet generation
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_or_null <= 1'b0;
        else if (tclk_count_store)
            tclk_or_null <= 1'b1;
        else if (lfsr_adv0)
            tclk_or_null <= 1'b0;
    end

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_packet <= 80'h0;
        else if (tclk_count_store)
            tclk_packet <= {8'h00, tclk_count_addr, 32'h00000000, tclk_count_d};
    end

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            aclk_packet <= 80'h0;
        else if (lfsr_adv0)
            aclk_packet <= tclk_or_null ? tclk_packet : 80'hFFFFFFFFFFFFFFFFFFFF;
    end

    // --------------------------------------------------------
    // PRPG
    // --------------------------------------------------------

    LFSR80 uPRPG_TX (
        .RESETn (RESETn),
        .CLK    (CLK1),
        .ADV    (lfsr_adv0),
        .LOAD   (1'b0),
        .D      (80'h0),
        .Q      (prpg_tx_output)
    );

    assign PR_PATT_80 = prpg_tx_output;

    assign prpg_tx_biterrs =
        {60'h0, ~ERROR_INPUTn, 19'h0};

    // --------------------------------------------------------
    // TCLK receiver
    // --------------------------------------------------------

    TCLK_RCV uTCLK_RCV (
        .RESETn       (RESETn),
        .CLK_40M      (CLK_40MHZ),
        .CLK_80M      (CLK_80MHZ),
        .TCLK         (TCLK),
        .TCLK_RATE    (1'b1),
        .DATA         (TCLK_DATA),
        .DAVn         (TCLK_DAVn),
        .SCLK         (),
        .TCLK_CAR     (),
        .TCLK07n      (),
        .PERR         (),
        .PERR_CLR     (1'b0),
        .SIG_ERR      (),
        .SIG_ERR_CLR  (1'b0)
    );

    // --------------------------------------------------------
    // Event count RAM
    // --------------------------------------------------------

    blk_mem_gen_0 uTCLK_EVENT_COUNT_RAM (
        .clka   (CLK1),
        .ena    (1'b1),
        .wea    (1'b0),
        .addra  (8'h00),
        .dina   (32'h0),
        .douta  (),
        .clkb   (CLK1),
        .enb    (1'b1),
        .web    ({tclk_count_store}),
        .addrb  (tclk_count_addr),
        .dinb   (tclk_count_d),
        .doutb  (tclk_count_q)
    );

    // --------------------------------------------------------
    // CRC
    // --------------------------------------------------------

    CRC8_CALC uCRC8_CALC_TX (
        .RESETn    (RESETn),
        .CLK       (CLK1),
        .CALC      (lfsr_adv1),
        .DATA      ({aclk_packet, 8'h00}),
        .CRC       (ACLK_TX_CRC),
        .CRC_VALID (ACLK_TX_CRC_VALID)
    );

    assign aclk_tx_noerr = {8'hBC, aclk_packet, ACLK_TX_CRC};
    assign aclk_tx_k     = 12'b100000000000;

    assign ACLK_TX_TO_GEARBOX =
        {aclk_tx_noerr[95:40],
         aclk_tx_noerr[39] ^ ~ERROR_INPUTn_edge,
         aclk_tx_noerr[38:0]};

    // --------------------------------------------------------
    // Error edge detect
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            ERROR_INPUTn_cap  <= 1'b1;
            ERROR_INPUTn_smpl <= 1'b1;
        end else if (ACLK_TX_TO_GEARBOX_VALID) begin
            ERROR_INPUTn_cap  <= ERROR_INPUTn;
            ERROR_INPUTn_smpl <= ERROR_INPUTn_cap;
        end
    end

    assign ERROR_INPUTn_edge = ERROR_INPUTn_cap | ~ERROR_INPUTn_smpl;

    assign DIAG[0] = ERROR_INPUTn_edge;
    assign DIAG[1] = CLK1;
    assign DIAG[3:2] = 2'b00;

    assign ACLK_TX_TO_GEARBOX_VALID = ACLK_TX_CRC_VALID;

    // --------------------------------------------------------
    // Gearbox
    // --------------------------------------------------------

    GEARBOX_96_TO_16 uTX_GEARBOX (
        .RESETn       (RESETn),
        .CLK1         (CLK1),
        .DATA96       (ACLK_TX_TO_GEARBOX),
        .K_IN         (12'b100000000000),
        .DATA96_VALID (ACLK_TX_TO_GEARBOX_VALID),
        .DATA16       (ACLK_DATA_OUT),
        .K_OUT        (ACLK_K_TO_XCVR)
    );

    // --------------------------------------------------------
    // Bit reverse (disabled by default)
    // --------------------------------------------------------

    integer n;
    always @(*) begin
        for (n = 0; n < 8; n = n + 1) begin
            ACLK_DATA_OUT_bitreverse[n]     = ACLK_DATA_OUT[7-n];
            ACLK_DATA_OUT_bitreverse[n + 8] = ACLK_DATA_OUT[15-n];
        end
    end

    assign ACLK_TX_TO_XCVR = ACLK_DATA_OUT;
    assign ACLK_TX_MARKER  = ACLK_TX_TO_GEARBOX_VALID;




    assign tclk_event_reg_debug = tclk_event_reg;
    assign TCLK_DAVn_debug = TCLK_DAVn;
    assign tclk_count_addr_debug = tclk_count_addr;
    assign tclk_count_d_debug = tclk_count_d; 
    assign tclk_count_store_debug = tclk_count_store;
    assign crnt_st_tclk_rcv_debug = crnt_st_tclk_rcv;
    assign tclk_or_null_debug = tclk_or_null;
    assign lfsr_adv0_debug = lfsr_adv0;
    assign lfsr_adv1_debug = lfsr_adv1;
    assign aclk_packet_debug = aclk_packet;
    assign tclk_packet_debug = tclk_packet;
    assign ACLK_TX_CRC_VALID_debug = ACLK_TX_CRC_VALID;
    assign ACLK_DATA_OUT_debug = ACLK_DATA_OUT;
endmodule


/*
// ------------------------------------------------------------
// ACLK_DATA_SOURCE.v
// Translated from VHDL
// ------------------------------------------------------------

module ACLK_DATA_SOURCE (
    input  wire        RESETn,
    input  wire        CLK1,
    input  wire        CLK_80MHZ,
    input  wire        CLK_40MHZ,

    output wire [15:0] ACLK_TX_TO_XCVR,
    output wire [1:0]  ACLK_K_TO_XCVR,

    input  wire        ERROR_INPUTn,
    input  wire        TCLK,

    output wire        ACLK_TX_MARKER,
    output wire [3:0]  DIAG, 
    output wire [7:0] tclk_event_reg_debug,
    output wire TCLK_DAVn_debug,
    output wire [7:0] tclk_count_addr_debug,
    output wire [31:0] tclk_count_d_debug, 
    output wire tclk_count_store_debug,
    output wire [7:0] crnt_st_tclk_rcv_debug
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
    reg  [15:0] ACLK_DATA_OUT_bitreverse;

    wire [95:0] aclk_tx_noerr;
    wire [11:0] aclk_tx_k;

    wire        TCLK_DAVn;
    wire [7:0]  TCLK_DATA;

    reg         tclk_count_store;
    reg         tclk_store_sel;
    reg         tclk_count_zero_ctr_clr;
    reg         tclk_or_null;

    reg  [7:0]  tclk_event_reg;
    reg  [7:0]  tclk_count_addr;
    reg  [7:0]  tclk_count_zero_ctr;

    reg  [31:0] tclk_count_d;
    wire [31:0] tclk_count_q;

    reg  [7:0]  crnt_st_tclk_rcv, next_st_tclk_rcv;

    wire [79:0] PR_PATT_80;
    wire [79:0] prpg_tx_output;
    wire [79:0] prpg_tx_biterrs;

    reg         ERROR_INPUTn_cap, ERROR_INPUTn_smpl;
    wire        ERROR_INPUTn_edge;

    // --------------------------------------------------------
    // LFSR advance counter
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            lfsr_adv_ctr <= 4'h0;
        else if (lfsr_adv_ctr == 4'h5)
            lfsr_adv_ctr <= 4'h0;
        else
            lfsr_adv_ctr <= lfsr_adv_ctr + 1'b1;
    end

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
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
    // Capture TCLK event
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_event_reg <= 8'h00;
        else if (!TCLK_DAVn)
            tclk_event_reg <= TCLK_DATA;
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

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_count_zero_ctr <= 8'h00;
        else if (tclk_count_zero_ctr_clr)
            tclk_count_zero_ctr <= 8'h00;
        else
            tclk_count_zero_ctr <= tclk_count_zero_ctr + 1'b1;
    end

    // --------------------------------------------------------
    // FSM
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            crnt_st_tclk_rcv <= 8'h00;
        else
            crnt_st_tclk_rcv <= next_st_tclk_rcv;
    end

    always @(*) begin
        case (crnt_st_tclk_rcv)

            8'h00: begin
                tclk_count_store           = 1'b1;
                tclk_store_sel             = 1'b0;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h01;
            end

            8'h01: begin
                tclk_count_store           = 1'b1;
                tclk_store_sel             = 1'b0;
                tclk_count_zero_ctr_clr    = 1'b0;
                next_st_tclk_rcv           =
                    (tclk_count_zero_ctr == 8'hFF) ? 8'h10 : 8'h01;
            end

            8'h10: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           =
                    (!TCLK_DAVn) ? 8'h11 : 8'h10;
            end

            8'h11: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h12;
            end

            8'h12: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h13;
            end

            8'h13: begin
                tclk_count_store           = 1'b1;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h10;
            end

            default: begin
                tclk_count_store           = 1'b0;
                tclk_store_sel             = 1'b1;
                tclk_count_zero_ctr_clr    = 1'b1;
                next_st_tclk_rcv           = 8'h00;
            end
        endcase
    end

    // --------------------------------------------------------
    // Packet generation
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_or_null <= 1'b0;
        else if (tclk_count_store)
            tclk_or_null <= 1'b1;
        else if (lfsr_adv0)
            tclk_or_null <= 1'b0;
    end

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            tclk_packet <= 80'h0;
        else if (tclk_count_store)
            tclk_packet <= {8'h00, tclk_count_addr, 32'h00000000, tclk_count_d};
    end

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn)
            aclk_packet <= 80'h0;
        else if (lfsr_adv0)
            aclk_packet <= tclk_or_null ? tclk_packet : 80'hFFFFFFFFFFFFFFFFFFFF;
    end

    // --------------------------------------------------------
    // PRPG
    // --------------------------------------------------------

    LFSR80 uPRPG_TX (
        .RESETn (RESETn),
        .CLK    (CLK1),
        .ADV    (lfsr_adv0),
        .LOAD   (1'b0),
        .D      (80'h0),
        .Q      (prpg_tx_output)
    );

    assign PR_PATT_80 = prpg_tx_output;

    assign prpg_tx_biterrs =
        {60'h0, ~ERROR_INPUTn, 19'h0};

    // --------------------------------------------------------
    // TCLK receiver
    // --------------------------------------------------------

    TCLK_RCV uTCLK_RCV (
        .RESETn       (RESETn),
        .CLK_40M      (CLK_40MHZ),
        .CLK_80M      (CLK_80MHZ),
        .TCLK         (TCLK),
        .TCLK_RATE    (1'b1),
        .DATA         (TCLK_DATA),
        .DAVn         (TCLK_DAVn),
        .SCLK         (),
        .TCLK_CAR     (),
        .TCLK07n      (),
        .PERR         (),
        .PERR_CLR     (1'b0),
        .SIG_ERR      (),
        .SIG_ERR_CLR  (1'b0)
    );

    // --------------------------------------------------------
    // Event count RAM
    // --------------------------------------------------------

    blk_mem_gen_0 uTCLK_EVENT_COUNT_RAM (
        .clka   (CLK1),
        .ena    (1'b1),
        .wea    (1'b0),
        .addra  (8'h00),
        .dina   (32'h0),
        .douta  (),
        .clkb   (CLK1),
        .enb    (1'b1),
        .web    (tclk_count_store),
        .addrb  (tclk_count_addr),
        .dinb   (tclk_count_d),
        .doutb  (tclk_count_q)
    );

    // --------------------------------------------------------
    // CRC
    // --------------------------------------------------------

    CRC8_CALC uCRC8_CALC_TX (
        .RESETn    (RESETn),
        .CLK       (CLK1),
        .CALC      (lfsr_adv1),
        .DATA      ({aclk_packet, 8'h00}),
        .CRC       (ACLK_TX_CRC),
        .CRC_VALID (ACLK_TX_CRC_VALID)
    );

    assign aclk_tx_noerr = {8'hBC, aclk_packet, ACLK_TX_CRC};
    assign aclk_tx_k     = 12'b100000000000;

    assign ACLK_TX_TO_GEARBOX =
        {aclk_tx_noerr[95:40],
         aclk_tx_noerr[39] ^ ~ERROR_INPUTn_edge,
         aclk_tx_noerr[38:0]};

    // --------------------------------------------------------
    // Error edge detect
    // --------------------------------------------------------

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            ERROR_INPUTn_cap  <= 1'b1;
            ERROR_INPUTn_smpl <= 1'b1;
        end else if (ACLK_TX_TO_GEARBOX_VALID) begin
            ERROR_INPUTn_cap  <= ERROR_INPUTn;
            ERROR_INPUTn_smpl <= ERROR_INPUTn_cap;
        end
    end

    assign ERROR_INPUTn_edge = ERROR_INPUTn_cap | ~ERROR_INPUTn_smpl;

    assign DIAG[0] = ERROR_INPUTn_edge;
    assign DIAG[1] = CLK1;
    assign DIAG[3:2] = 2'b00;

    assign ACLK_TX_TO_GEARBOX_VALID = ACLK_TX_CRC_VALID;

    // --------------------------------------------------------
    // Gearbox
    // --------------------------------------------------------

    GEARBOX_96_TO_16 uTX_GEARBOX (
        .RESETn       (RESETn),
        .CLK1         (CLK1),
        .DATA96       (ACLK_TX_TO_GEARBOX),
        .K_IN         (12'b100000000000),
        .DATA96_VALID (ACLK_TX_TO_GEARBOX_VALID),
        .DATA16       (ACLK_DATA_OUT),
        .K_OUT        (ACLK_K_TO_XCVR)
    );

    // --------------------------------------------------------
    // Bit reverse (disabled by default)
    // --------------------------------------------------------

    integer n;
    always @(*) begin
        for (n = 0; n < 8; n = n + 1) begin
            ACLK_DATA_OUT_bitreverse[n]     = ACLK_DATA_OUT[7-n];
            ACLK_DATA_OUT_bitreverse[n + 8] = ACLK_DATA_OUT[15-n];
        end
    end

    assign ACLK_TX_TO_XCVR = ACLK_DATA_OUT;
    assign ACLK_TX_MARKER  = ACLK_TX_TO_GEARBOX_VALID;




    assign tclk_event_reg_debug = tclk_event_reg;
    assign TCLK_DAVn_debug = TCLK_DAVn;
    assign tclk_count_addr_debug = tclk_count_addr;
    assign tclk_count_d_debug = tclk_count_d; 
    assign tclk_count_store_debug = tclk_count_store;
    assign crnt_st_tclk_rcv_debug = crnt_st_tclk_rcv;
    
endmodule
*/