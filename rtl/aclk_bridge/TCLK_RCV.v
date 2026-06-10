// ------------------------------------------------------------
// TCLK_RCV.v
// Translated from VHDL
// ------------------------------------------------------------

module TCLK_RCV (
    input  wire        RESETn,
    input  wire        CLK_40M,
    input  wire        CLK_80M,
    input  wire        TCLK,
    input  wire        TCLK_RATE,

    output wire [7:0]  DATA,
    output wire        DAVn,
    output wire        SCLK,
    output wire        TCLK_CAR,
    output reg         TCLK07n,
    output wire        PERR,
    input  wire        PERR_CLR,
    output wire        SIG_ERR,
    input  wire        SIG_ERR_CLR
);

    // --------------------------------------------------------
    // Internal signals
    // --------------------------------------------------------

    wire        SCLK_int;
    wire        SDATA;
    wire        DAVn_int;
    wire [7:0]  DATA_int;

    wire        SIG_ERR_detect;
    reg         SIG_ERR_int;

    // --------------------------------------------------------
    // TCLK decoder / serializer
    // --------------------------------------------------------

    serdec4_9MHz uTCLK_DECODER (
        .RESETn    (RESETn),
        .CLK_80M   (CLK_80M),
        .TCLK      (TCLK),
        .RATE      (TCLK_RATE),
        .SCLK      (SCLK_int),
        .SDATA     (SDATA),
        .TCLK_CAR  (TCLK_CAR),
        .SIG_ERR   (SIG_ERR_detect)
    );

    // --------------------------------------------------------
    // TCLK deserializer
    // --------------------------------------------------------

    TCLK_DESERIALIZER2 uTCLK_DESERIALIZER (
        .RESETn    (RESETn),
        .CLK_40M   (CLK_40M),
        .SCLK      (SCLK_int),
        .SDATA     (SDATA),
        .DATA_OUT  (DATA_int),
        .DAVn      (DAVn_int),
        .PERR      (PERR),
        .PERR_CLR  (PERR_CLR)
    );

    // --------------------------------------------------------
    // Detect special value 0x07
    // --------------------------------------------------------

    always @(*) begin
        if (!DAVn_int && (DATA_int == 8'h07))
            TCLK07n = 1'b0;
        else
            TCLK07n = 1'b1;
    end

    // --------------------------------------------------------
    // Outputs
    // --------------------------------------------------------

    assign DATA = DATA_int;
    assign DAVn = DAVn_int;
    assign SCLK = SCLK_int;

    // --------------------------------------------------------
    // Latch the TCLK signal error
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn)
            SIG_ERR_int <= 1'b0;
        else if (SIG_ERR_detect)
            SIG_ERR_int <= 1'b1;
        else if (SIG_ERR_CLR)
            SIG_ERR_int <= 1'b0;
        else
            SIG_ERR_int <= SIG_ERR_int; // explicit hold
    end

    assign SIG_ERR = SIG_ERR_int;

endmodule
