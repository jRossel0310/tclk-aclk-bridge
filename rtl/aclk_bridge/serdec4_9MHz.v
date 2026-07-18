// ------------------------------------------------------------
// serdec4_9MHz.v  (parameterized by OSR = CLK_80M / 10 MHz oversample ratio)
// Default OSR=8 reproduces the original 80 MHz taps bit-for-bit on decode.
// OSR=40 supports the 400 MHz oversample of the 5 ns TCLK build.
// ------------------------------------------------------------

module serdec4_9MHz #(
    parameter int OSR = 8               // CLK_80M samples per 100 ns TCLK bit-cell
) (
    input  wire RESETn,
    input  wire CLK_80M,
    input  wire TCLK,
    input  wire RATE,

    output wire SCLK,
    output wire SDATA,
    output wire TCLK_CAR,
    output wire SIG_ERR
);
    // Widest referenced sample is ~1.5 cells back (12 at OSR=8 -> 13-bit shifter).
    localparam int DELW = (3*OSR)/2 + 1;

    reg  [7:0]  crnt_st_decode, next_st_decode;
    reg  [7:0]  crnt_st_data,   next_st_data;

    reg  [DELW-1:0] TCLK_del;

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

    // ---- TCLK delay shift register ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            TCLK_del <= '0;
        else
            TCLK_del <= {TCLK_del[DELW-2:0], TCLK};
    end

    // Immediate edge (transition "now", a fixed 1-sample detection pipeline). NOTE:
    // its physical glitch-rejection window shrinks ~5x at 400 MHz; see the spec's
    // "real-line edge-noise" risk. Kept at [1]/[2] for the clean-decode build; a
    // parameterized debounce is a follow-up if bring-up shows line-noise sensitivity.
    assign TCLK_posedge =  TCLK_del[1] & ~TCLK_del[2];
    assign TCLK_negedge =  TCLK_del[2] & ~TCLK_del[1];

    // Delayed edge (~1 bit-cell back). RATE=1 (10 MHz): one cell = OSR samples.
    always @(*) begin
        if (RATE) begin // 10 MHz
            TCLK_del_posedge =  TCLK_del[OSR-1] & ~TCLK_del[OSR];
            TCLK_del_negedge =  TCLK_del[OSR]   & ~TCLK_del[OSR-1];
        end else begin
            TCLK_del_posedge =  TCLK_del[OSR]   & ~TCLK_del[OSR+1];
            TCLK_del_negedge =  TCLK_del[OSR+1] & ~TCLK_del[OSR];
        end
    end

    // ---- Decode FSM (registered state) - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn) crnt_st_decode <= 8'h00;
        else         crnt_st_decode <= next_st_decode;
    end

    // ---- Decode FSM (combinational) - UNCHANGED ----
    always @(*) begin
        one_detect  = 1'b0;
        zero_detect = 1'b0;
        next_st_decode = crnt_st_decode;
        case (crnt_st_decode)
            8'h00: if (TCLK_del_posedge) next_st_decode = 8'h10;
            8'h10: begin
                if (TCLK_posedge)      begin one_detect  = 1'b1; next_st_decode = 8'h00; end
                else if (TCLK_negedge) begin zero_detect = 1'b1; next_st_decode = 8'h20; end
            end
            8'h20: if (TCLK_del_negedge) next_st_decode = 8'h30;
            8'h30: begin
                if (TCLK_negedge)      begin one_detect  = 1'b1; next_st_decode = 8'h20; end
                else if (TCLK_posedge) begin zero_detect = 1'b1; next_st_decode = 8'h00; end
            end
            default: next_st_decode = 8'h00;
        endcase
    end

    // ---- Data FSM (registered state) - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn) crnt_st_data <= 8'h00;
        else         crnt_st_data <= next_st_data;
    end

    // ---- Data FSM (combinational) - UNCHANGED ----
    always @(*) begin
        SCLK_set = 1'b0; SCLK_clr = 1'b0;
        SDATA_set = 1'b0; SDATA_clr = 1'b0;
        tclk_gate_cap = 1'b0;
        next_st_data = crnt_st_data;
        case (crnt_st_data)
            8'h00: begin
                SCLK_clr = 1'b1;
                if (one_detect)      begin SDATA_set = 1'b1; next_st_data = 8'h10; end
                else if (zero_detect) begin SDATA_clr = 1'b1; next_st_data = 8'h10; end
            end
            8'h10: begin SCLK_set = 1'b1; next_st_data = 8'h11; end
            8'h11: next_st_data = 8'h12;
            8'h12: begin tclk_gate_cap = 1'b1; next_st_data = 8'h13; end
            8'h13: next_st_data = 8'h00;
            default: next_st_data = 8'h00;
        endcase
    end

    // ---- SCLK / SDATA / carrier - UNCHANGED ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)        SCLK_int <= 1'b0;
        else if (SCLK_clr)  SCLK_int <= 1'b0;
        else if (SCLK_set)  SCLK_int <= 1'b1;
    end
    assign SCLK = SCLK_int;

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)         SDATA_int <= 1'b1;
        else if (SDATA_clr)  SDATA_int <= 1'b0;
        else if (SDATA_set)  SDATA_int <= 1'b1;
    end
    assign SDATA = SDATA_int;

    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)            tclk_gate <= 1'b0;
        else if (tclk_gate_cap) tclk_gate <= TCLK;
    end
    assign TCLK_CAR = TCLK ^ tclk_gate;

    // ---- Signal-error: illegal biphase run lengths, parameterized by OSR ----
    // Detects a run of exactly L samples of one level bounded by the opposite level,
    // using window TCLK_del[L+2 : 1]. Illegal lengths for RATE=1 are 1, 2, OSR-2 and
    // 1.5*OSR-2 (matching the original 1,2,6,10 at OSR=8); 3 and OSR-1 add for RATE=0.
    localparam int LA = 1;
    localparam int LB = 2;
    localparam int LC = OSR - 2;
    localparam int LD = (3*OSR)/2 - 2;
    localparam int LE = 3;              // RATE=0 only
    localparam int LF = OSR - 1;        // RATE=0 only

    // run of L zeros:  TCLK_del[L+2]=1, TCLK_del[L+1:2]=0, TCLK_del[1]=1
    // run of L ones:   TCLK_del[L+2]=0, TCLK_del[L+1:2]=1, TCLK_del[1]=0
    // Constant-width comparisons (LA..LF are localparams, so +: widths are constant).
    wire err_a = ( TCLK_del[LA+2] & ~(|TCLK_del[2 +: LA]) & TCLK_del[1])
               | (~TCLK_del[LA+2] &  (&TCLK_del[2 +: LA]) & ~TCLK_del[1]);
    wire err_b = ( TCLK_del[LB+2] & ~(|TCLK_del[2 +: LB]) & TCLK_del[1])
               | (~TCLK_del[LB+2] &  (&TCLK_del[2 +: LB]) & ~TCLK_del[1]);
    wire err_c = ( TCLK_del[LC+2] & ~(|TCLK_del[2 +: LC]) & TCLK_del[1])
               | (~TCLK_del[LC+2] &  (&TCLK_del[2 +: LC]) & ~TCLK_del[1]);
    wire err_d = ( TCLK_del[LD+2] & ~(|TCLK_del[2 +: LD]) & TCLK_del[1])
               | (~TCLK_del[LD+2] &  (&TCLK_del[2 +: LD]) & ~TCLK_del[1]);
    wire err_e = ( TCLK_del[LE+2] & ~(|TCLK_del[2 +: LE]) & TCLK_del[1])
               | (~TCLK_del[LE+2] &  (&TCLK_del[2 +: LE]) & ~TCLK_del[1]);
    wire err_f = ( TCLK_del[LF+2] & ~(|TCLK_del[2 +: LF]) & TCLK_del[1])
               | (~TCLK_del[LF+2] &  (&TCLK_del[2 +: LF]) & ~TCLK_del[1]);

    always @(*) begin
        sig_err_detect = err_a | err_b | err_c | err_d | ((err_e | err_f) & ~RATE);
    end

    // ---- Error stretch counter - UNCHANGED ----
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
