// ============================================================
// ACLK_RCV.v
// Translated from VHDL to Verilog
// ============================================================

module ACLK_RCV (
    input  wire        RESETn,
    input  wire        CLK1,

    input  wire [15:0] DATA_FROM_XCVR,
    input  wire [1:0]  K_FROM_XCVR,

    output wire [15:0] ACLK_EVENT,
    output wire [63:0] ACLK_DATA,
    output reg         ACLK_VALID,
    output reg         ACLK_ERROR,
    output wire        RX_ALIGNED_OUT,
    output wire [3:0]  DIAG
);

    // ============================================================
    // Internal Signals
    // ============================================================

    wire [15:0] DATA_FROM_XCVR_bitrev;

    wire        ACLK_DATA_VALID_TO_RX_CHECK;
    wire [87:0] input_to_rx_crc_checker;
    wire [7:0]  ACLK_RX_CRC;
    wire        ACLK_RX_CRC_VALID;
    reg         RX_CRC_EQ_ZERO;

    reg  [3:0]  rx_pickup_ctr;
    reg  [3:0]  rx_dropout_ctr;
    reg         rx_aligned;

    wire [95:0] data96_from_rx_gearbox;
    wire [11:0] k_from_gearbox;
    wire        data96_valid_from_rx_gearbox;

    wire        SEQ_CTR_3;

    // ============================================================
    // Byte-wise Bit Reverse (combinational)
    // ============================================================

    genvar n;
    generate
        for (n = 0; n < 8; n = n + 1) begin : BITREV
            assign DATA_FROM_XCVR_bitrev[n]     = DATA_FROM_XCVR[7-n];
            assign DATA_FROM_XCVR_bitrev[8+n]   = DATA_FROM_XCVR[15-n];
        end
    endgenerate

    // ============================================================
    // Gearbox Instance
    // ============================================================

    GEARBOX_16_TO_96 uRX_GEARBOX (
        .RESETn       (RESETn),
        .CLK1         (CLK1),
        .DATA16       (DATA_FROM_XCVR),
        // .DATA16     (DATA_FROM_XCVR_bitrev),  // optional
        .K_IN         (K_FROM_XCVR),
        .DATA96       (data96_from_rx_gearbox),
        .K_OUT        (k_from_gearbox),
        .DATA96_VALID (data96_valid_from_rx_gearbox),
        .SEQ_CTR_3    (SEQ_CTR_3)
    );

    assign ACLK_DATA_VALID_TO_RX_CHECK = data96_valid_from_rx_gearbox;
    assign input_to_rx_crc_checker     = data96_from_rx_gearbox[87:0];

    // ============================================================
    // CRC8 Calculator Instance
    // ============================================================

    CRC8_CALC uCRC8_CALC_RX (
        .RESETn    (RESETn),
        .CLK       (CLK1),
        .CALC      (ACLK_DATA_VALID_TO_RX_CHECK),
        .DATA      (input_to_rx_crc_checker),
        .CRC       (ACLK_RX_CRC),
        .CRC_VALID (ACLK_RX_CRC_VALID)
    );

    // ============================================================
    // CRC == 0 Detection (combinational)
    // ============================================================

    always @(*) begin
        if (ACLK_RX_CRC == 8'h00)
            RX_CRC_EQ_ZERO = 1'b1;
        else
            RX_CRC_EQ_ZERO = 1'b0;
    end

    // ============================================================
    // Alignment Detection Logic
    // ============================================================

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            rx_pickup_ctr  <= 4'h0;
            rx_dropout_ctr <= 4'h0;
            rx_aligned     <= 1'b0;
        end
        else begin
            if (ACLK_RX_CRC_VALID && RX_CRC_EQ_ZERO) begin
                rx_dropout_ctr <= 4'h0;

                if (rx_pickup_ctr == 4'h4) begin
                    rx_aligned <= 1'b1;
                end
                else begin
                    rx_pickup_ctr <= rx_pickup_ctr + 1'b1;
                end
            end

            else if (ACLK_RX_CRC_VALID && !RX_CRC_EQ_ZERO) begin
                rx_pickup_ctr <= 4'h0;

                if (rx_dropout_ctr == 4'h4) begin
                    rx_aligned <= 1'b0;
                end
                else begin
                    rx_dropout_ctr <= rx_dropout_ctr + 1'b1;
                end
            end
        end
    end

    assign RX_ALIGNED_OUT = rx_aligned;

    // ============================================================
    // Output Extraction
    // ============================================================

    assign ACLK_EVENT = data96_from_rx_gearbox[87:72];
    assign ACLK_DATA  = data96_from_rx_gearbox[71:8];

    // ============================================================
    // VALID / ERROR Generation
    // ============================================================

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            ACLK_VALID <= 1'b0;
            ACLK_ERROR <= 1'b0;
        end
        else begin
            ACLK_VALID <= ACLK_RX_CRC_VALID &&  RX_CRC_EQ_ZERO  && rx_aligned;
            ACLK_ERROR <= ACLK_RX_CRC_VALID && !RX_CRC_EQ_ZERO && rx_aligned;
        end
    end

    // ============================================================
    // Diagnostic Outputs
    // ============================================================

    assign DIAG[0] = RX_CRC_EQ_ZERO;
    assign DIAG[1] = k_from_gearbox[11];
    assign DIAG[2] = SEQ_CTR_3;
    assign DIAG[3] = 1'b0;   // unused (matches VHDL behavior)

endmodule