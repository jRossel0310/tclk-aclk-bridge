// ------------------------------------------------------------
// serdec4_9MHz.v  (parameterized by OSR = CLK_80M / 10 MHz oversample ratio)
// Default OSR=8 reproduces the original 80 MHz taps bit-for-bit on decode.
// OSR=40 supports the 400 MHz oversample of the 5 ns TCLK build.
// ------------------------------------------------------------

module serdec4_9MHz #(
    parameter integer OSR = 8               // CLK_80M samples per 100 ns TCLK bit-cell
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
    localparam integer DELW = (3*OSR)/2 + 1;

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

    // Glitch-reject debounce on the raw TCLK line, scaled by OSR so the physical
    // rejection window (~DB samples) stays near the baseline. At 400 MHz the raw
    // sampler resolves real-line ringing that the 80 MHz sampler averaged out; those
    // extra edges corrupt the biphase decode (high PERR on the live line). DB=0 at
    // OSR<=8 makes this a pass-through, so OSR=8 decode is bit-identical to the
    // original. TUNING KNOB: the /6 sets the window (OSR/6 = 6 samples = 15 ns at
    // OSR=40); /4 (10 samples) over-filtered and cost clean-decode margin near the
    // serdec lock-on transient, so this was walked back to /6. Lower it further if it
    // still over-filters clean decode, raise it (carefully, re-verify the margin) if
    // the real line turns out dirtier than the width=3 ringing case tb/tclk_rcv
    // covers.
    localparam integer DB = (OSR > 8) ? (OSR/6) : 0;
    wire tclk_clean;
    generate
    if (DB == 0) begin : g_nofilt
        assign tclk_clean = TCLK;
    end else begin : g_filt
        reg       tclk_db;
        reg [7:0] db_cnt;
        always @(posedge CLK_80M or negedge RESETn) begin
            if (!RESETn) begin
                tclk_db <= 1'b1;             // idle high, matching the TCLK idle
                db_cnt  <= 8'd0;
            end else if (TCLK == tclk_db) begin
                db_cnt  <= 8'd0;            // agrees with debounced level: reset
            end else if (db_cnt >= DB - 1) begin
                tclk_db <= TCLK;            // held the new level DB samples: accept
                db_cnt  <= 8'd0;
            end else begin
                db_cnt  <= db_cnt + 8'd1;   // differs but not yet stable long enough
            end
        end
        assign tclk_clean = tclk_db;
    end
    endgenerate

    // ---- TCLK delay shift register ----
    always @(posedge CLK_80M or negedge RESETn) begin
        if (!RESETn)
            TCLK_del <= {DELW{1'b0}};
        else
            TCLK_del <= {TCLK_del[DELW-2:0], tclk_clean};   // debounced input (glitch-rejected)
    end

    // Immediate edge (transition "now", a fixed 1-sample detection pipeline), taken
    // on TCLK_del which is fed by tclk_clean (see the DB debounce above), not raw
    // TCLK. Real-line ringing/edge-noise at 400 MHz is rejected upstream by that
    // debounce, so this [1]/[2] edge detect only ever sees already-clean transitions.
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
            8'h12: begin
                tclk_gate_cap = 1'b1; // empirically placed
                next_st_data = 8'h13;
            end
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
        else if (tclk_gate_cap)
            tclk_gate <= TCLK; // matches VHDL (not inverted)
    end
    assign TCLK_CAR = TCLK ^ tclk_gate;

    // ---- Signal-error: illegal biphase run lengths, parameterized by OSR ----
    // Detects a run of exactly L samples of one level bounded by the opposite level,
    // using window TCLK_del[L+2 : 1]. Illegal lengths for RATE=1 are 1, 2, OSR-2 and
    // 1.5*OSR-2 (matching the original 1,2,6,10 at OSR=8); 3 and OSR-1 add for RATE=0.
    localparam integer LA = 1;
    localparam integer LB = 2;
    localparam integer LC = OSR - 2;
    localparam integer LD = (3*OSR)/2 - 2;
    localparam integer LE = 3;              // RATE=0 only
    localparam integer LF = OSR - 1;        // RATE=0 only

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
