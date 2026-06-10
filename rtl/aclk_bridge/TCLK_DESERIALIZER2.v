// ------------------------------------------------------------
// TCLK_DESERIALIZER2.v
// Translated from VHDL
// ------------------------------------------------------------

module TCLK_DESERIALIZER2 (
    input  wire        RESETn,
    input  wire        CLK_40M,
    input  wire        SCLK,
    input  wire        SDATA,

    output wire [7:0]  DATA_OUT,
    output reg         DAVn,
    output wire        PERR,
    input  wire        PERR_CLR
);

    // --------------------------------------------------------
    // Internal signals
    // --------------------------------------------------------

    reg  [10:0] data_reg;
    reg  [7:0]  DATA_OUT_int;

    reg  parity_reg;
    reg  parity_calc;

    reg  DAVn_int;

    reg  SCLK_cap, SCLK_smpl, SCLK_edge;
    wire SCLK_posedge;

    reg  SDATA_cap, SDATA_smpl;

    reg  [3:0] crnt_st_deser, next_st_deser;

    reg  [3:0] SCLK_ctr;
    reg        SCLK_ctr_clr, SCLK_ctr_incr;

    reg  PERR_int, PERR_int_set;

    // --------------------------------------------------------
    // SCLK synchronizer and edge detection
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn) begin
            SCLK_cap  <= 1'b0;
            SCLK_smpl <= 1'b0;
            SCLK_edge <= 1'b0;
        end else begin
            SCLK_cap  <= SCLK;
            SCLK_smpl <= SCLK_cap;
            SCLK_edge <= SCLK_smpl;
        end
    end

    assign SCLK_posedge = SCLK_smpl & ~SCLK_edge;

    // --------------------------------------------------------
    // SDATA synchronizer
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn) begin
            SDATA_cap  <= 1'b1;
            SDATA_smpl <= 1'b1;
        end else begin
            SDATA_cap  <= SDATA;
            SDATA_smpl <= SDATA_cap;
        end
    end

    // --------------------------------------------------------
    // Shift register and parity calculation
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn) begin
            data_reg    <= 11'b111_1111_1111;
            parity_reg  <= 1'b1;
            parity_calc <= 1'b1;
        end else begin
            if (SCLK_posedge) begin
                data_reg    <= {data_reg[9:0], parity_reg};
                parity_reg  <= SDATA_smpl;
                parity_calc <= parity_calc ^ parity_reg ^ data_reg[10];
            end
        end
    end

    // --------------------------------------------------------
    // Deserializer FSM state register
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn)
            crnt_st_deser <= 4'h0;
        else if (SCLK_posedge)
            crnt_st_deser <= next_st_deser;
    end

    // --------------------------------------------------------
    // Deserializer FSM (combinational)
    // --------------------------------------------------------

    always @(*) begin
        // defaults
        SCLK_ctr_clr  = 1'b0;
        SCLK_ctr_incr = 1'b0;
        DAVn_int      = 1'b1;
        PERR_int_set  = 1'b0;
        next_st_deser = crnt_st_deser;

        case (crnt_st_deser)

            4'h0: begin
                SCLK_ctr_clr = 1'b1;

                if (data_reg[10:8] == 3'b110) begin
                    if (parity_reg == parity_calc) begin
                        DAVn_int      = 1'b0;
                        next_st_deser = 4'h1;
                    end else begin
                        PERR_int_set  = 1'b1;
                        next_st_deser = 4'h0;
                    end
                end
            end

            // Inject a 10-SCLK delay before next detection
            4'h1: begin
                if (SCLK_ctr == 4'hA) begin
                    next_st_deser = 4'h0;
                end else begin
                    SCLK_ctr_incr = SCLK_posedge;
                    next_st_deser = 4'h1;
                end
            end

            default: begin
                next_st_deser = 4'h0;
            end
        endcase
    end

    // --------------------------------------------------------
    // SCLK counter
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn)
            SCLK_ctr <= 4'h0;
        else if (SCLK_ctr_clr)
            SCLK_ctr <= 4'h0;
        else if (SCLK_ctr_incr)
            SCLK_ctr <= SCLK_ctr + 4'd1;
    end

    // --------------------------------------------------------
    // DAVn output (one-cycle strobe)
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn)
            DAVn <= 1'b1;
        else
            // Strobe DAVn low for only one clock cycle
            DAVn <= DAVn_int | ~SCLK_posedge;
    end

    // --------------------------------------------------------
    // Data output register
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn)
            DATA_OUT_int <= 8'hFF;
        else if (!DAVn_int)
            DATA_OUT_int <= data_reg[7:0];
    end

    assign DATA_OUT = DATA_OUT_int;

    // --------------------------------------------------------
    // Parity error latch
    // --------------------------------------------------------

    always @(posedge CLK_40M or negedge RESETn) begin
        if (!RESETn)
            PERR_int <= 1'b0;
        else if (PERR_int_set)
            PERR_int <= 1'b1;
        else if (PERR_CLR)
            PERR_int <= 1'b0;
    end

    assign PERR = PERR_int;

endmodule
