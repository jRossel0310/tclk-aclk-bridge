// ------------------------------------------------------------
// serdec4_9MHz.v
// Translated from VHDL
// ------------------------------------------------------------

module serdec4_9MHz (
    input  wire RESETn,
    input  wire CLK_80M,
    input  wire TCLK,
    input  wire RATE,

    output wire SCLK,
    output wire SDATA,
    output wire TCLK_CAR,
    output wire SIG_ERR
);

    // --------------------------------------------------------
    // Internal signals
    // --------------------------------------------------------

    reg  [7:0]  crnt_st_decode, next_st_decode;
    reg  [7:0]  crnt_st_data,   next_st_data;

    reg  [12:0] TCLK_del;

    wire TCLK_posedge;
    wire TCLK_negedge;
    reg  TCLK_del_posedge;
    reg  TCLK_del_negedge;

    reg  one_detect, zero_detect;

    reg  SCLK_int,  SCLK_set,  SCLK_clr;
    reg  SDATA_int, SDATA_set, SDATA_clr;

    reg  tclk_gate, tclk_gate_cap;

    reg        sig_err_detect;
    reg  [2:0] sig_err_stretch;

    // --------------------------------------------------------
    // TCLK delay shift register
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            TCLK_del <= 13'b0;
        else
            TCLK_del <= {TCLK_del[11:0], TCLK};
    end

    assign TCLK_posedge =  TCLK_del[1] & ~TCLK_del[2];
    assign TCLK_negedge =  TCLK_del[2] & ~TCLK_del[1];

    // --------------------------------------------------------
    // Rate-dependent delayed edge detect
    // --------------------------------------------------------

    always @(*) begin
        if (RATE) begin // 10 MHz
            TCLK_del_posedge =  TCLK_del[7] & ~TCLK_del[8];
            TCLK_del_negedge =  TCLK_del[8] & ~TCLK_del[7];
        end else begin
            TCLK_del_posedge =  TCLK_del[8] & ~TCLK_del[9];
            TCLK_del_negedge =  TCLK_del[9] & ~TCLK_del[8];
        end
    end

    // --------------------------------------------------------
    // Decode FSM (registered state)
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            crnt_st_decode <= 8'h00;
        else
            crnt_st_decode <= next_st_decode;
    end

    // --------------------------------------------------------
    // Decode FSM (combinational)
    // --------------------------------------------------------

    always @(*) begin
        one_detect  = 1'b0;
        zero_detect = 1'b0;
        next_st_decode = crnt_st_decode;

        case (crnt_st_decode)

            8'h00: begin
                if (TCLK_del_posedge)
                    next_st_decode = 8'h10;
            end

            8'h10: begin
                if (TCLK_posedge) begin
                    one_detect  = 1'b1;
                    next_st_decode = 8'h00;
                end else if (TCLK_negedge) begin
                    zero_detect = 1'b1;
                    next_st_decode = 8'h20;
                end
            end

            8'h20: begin
                if (TCLK_del_negedge)
                    next_st_decode = 8'h30;
            end

            8'h30: begin
                if (TCLK_negedge) begin
                    one_detect  = 1'b1;
                    next_st_decode = 8'h20;
                end else if (TCLK_posedge) begin
                    zero_detect = 1'b1;
                    next_st_decode = 8'h00;
                end
            end

            default: begin
                next_st_decode = 8'h00;
            end
        endcase
    end

    // --------------------------------------------------------
    // Data FSM (registered state)
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            crnt_st_data <= 8'h00;
        else
            crnt_st_data <= next_st_data;
    end

    // --------------------------------------------------------
    // Data FSM (combinational)
    // --------------------------------------------------------

    always @(*) begin
        SCLK_set = 1'b0;
        SCLK_clr = 1'b0;
        SDATA_set = 1'b0;
        SDATA_clr = 1'b0;
        tclk_gate_cap = 1'b0;
        next_st_data = crnt_st_data;

        case (crnt_st_data)

            8'h00: begin
                SCLK_clr = 1'b1;
                if (one_detect) begin
                    SDATA_set = 1'b1;
                    next_st_data = 8'h10;
                end else if (zero_detect) begin
                    SDATA_clr = 1'b1;
                    next_st_data = 8'h10;
                end
            end

            8'h10: begin
                SCLK_set = 1'b1;
                next_st_data = 8'h11;
            end

            8'h11: begin
                next_st_data = 8'h12;
            end

            8'h12: begin
                tclk_gate_cap = 1'b1; // empirically placed
                next_st_data = 8'h13;
            end

            8'h13: begin
                next_st_data = 8'h00;
            end

            default: begin
                next_st_data = 8'h00;
            end
        endcase
    end

    // --------------------------------------------------------
    // SCLK register
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            SCLK_int <= 1'b0;
        else if (SCLK_clr)
            SCLK_int <= 1'b0;
        else if (SCLK_set)
            SCLK_int <= 1'b1;
    end

    assign SCLK = SCLK_int;

    // --------------------------------------------------------
    // SDATA register
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            SDATA_int <= 1'b1;
        else if (SDATA_clr)
            SDATA_int <= 1'b0;
        else if (SDATA_set)
            SDATA_int <= 1'b1;
    end

    assign SDATA = SDATA_int;

    // --------------------------------------------------------
    // TCLK carrier generation
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            tclk_gate <= 1'b0;
        else if (tclk_gate_cap)
            tclk_gate <= TCLK; // matches VHDL (not inverted)
    end

    assign TCLK_CAR = TCLK ^ tclk_gate;

    // --------------------------------------------------------
    // Signal error detection (combinational)
    // --------------------------------------------------------

    always @(*) begin
        if (   (TCLK_del[3:1]  == 3'b101) || (TCLK_del[3:1]  == 3'b010)
            || (TCLK_del[4:1]  == 4'b1001)|| (TCLK_del[4:1]  == 4'b0110)
            || (((TCLK_del[5:1]  == 5'b10001) || (TCLK_del[5:1]  == 5'b01110)) && !RATE)
            || (TCLK_del[8:1]  == 8'b10000001) || (TCLK_del[8:1]  == 8'b01111110)
            || (((TCLK_del[9:1]  == 9'b100000001) || (TCLK_del[9:1]  == 9'b011111110)) && !RATE)
            || (TCLK_del[12:1] == 12'b100000000001) || (TCLK_del[12:1] == 12'b011111111110)
           )
            sig_err_detect = 1'b1;
        else
            sig_err_detect = 1'b0;
    end

    // --------------------------------------------------------
    // Error stretch counter
    // --------------------------------------------------------

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            sig_err_stretch <= 3'b011;
        else if ((sig_err_stretch == 3'b011) && !sig_err_detect)
            sig_err_stretch <= sig_err_stretch;
        else
            sig_err_stretch <= sig_err_stretch + 3'b001;
    end

    assign SIG_ERR = sig_err_stretch[2];

endmodule
