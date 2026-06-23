// rtl/aclk_gt/aclk_gt_frame_gen.v
//
// Compiled-in gigabit-ACLK frame generator (IP-free, Icarus-simmable). Cycles a
// fixed event/data timeline, builds {0xBC, EVENT[15:0], DATA[63:0], CRC8} using
// the inherited CRC8_CALC, and feeds GEARBOX_96_TO_16 to emit the 16-bit + K word
// stream a GT TX serializes. Replaces aclk_data_source (which needs a BRAM IP and
// a TCLK source). CRC8 is computed over {packet80, 8'h00} and appended, matching
// crc8_calc.v / tb/aclk_tx_model.build_frame.
//
// Gearbox timing (verified against gearbox_96_to_16.v):
//   DATA96_VALID asserted and registered by the gearbox at posedge G means:
//     G+1: counter=0 -> word0; ... G+6: counter=5 -> word5; G+7: default 0x0000.
//   For gapless back-to-back frames, the NEXT DATA96_VALID must be registered at
//   G+6 (same posedge as word5), which requires the FSM to set data96_valid at
//   posedge G+5.
//
// CRC pipeline (crc8_calc.v):
//   Setting crc_calc (registered) at posedge P makes CRC8_CALC see CALC=1 at
//   posedge P+1. CRC_VALID is then seen at posedge P+1+3 = P+4.
//   So crc_calc set at posedge P -> CRC_VALID seen at posedge P+4.
//
// Steady-state pipeline (one frame = 6 emit_ctr steps, G = emit_ctr=0):
//   The FSM sets crc_calc at emit_ctr=5 of frame N (= posedge G+5). CRC8_CALC
//   sees CALC=1 at G+6 = next frame's emit_ctr=0. CRC_VALID arrives at emit_ctr=3
//   of the next frame (G+6+3 = G+9 = frame N+1 ctr=3).
//   At emit_ctr=3: latch next_data96.
//   At emit_ctr=5: fire DATA96_VALID (gearbox sees it at G'+6=next-next frame G'').
//
// Warmup (5 cycles before the first DATA96_VALID):
//   warm_ctr=0: set crc_calc + crc_data for frame0. CRC8_CALC sees CALC at ctr=1.
//   warm_ctr=4: CRC_VALID for frame0 fires. Fire DATA96_VALID for frame0. Also set
//               crc_calc for frame1 so CRC8_CALC sees CALC at emit_ctr=0 (next posedge).
//               Enter S_RUN with emit_ctr=0.
module aclk_gt_frame_gen #(
    parameter integer N_EVENTS = 3
) (
    input  wire        CLK1,
    input  wire        RESETn,
    output wire [15:0] DATA16,
    output wire [1:0]  K_OUT,
    output reg         MARKER          // high for one cycle per DATA96_VALID
);
    // Guard: ev_rom/da_rom are hardcoded to 3 entries; N_EVENTS must equal 3.
    generate
        if (N_EVENTS != 3)
            $error("aclk_gt_frame_gen: ev_rom/da_rom hardcoded to 3 entries; N_EVENTS must be 3");
    endgenerate

    // ---- compiled-in timeline (MUST match tb/aclkgt_gen/test_aclkgt_gen.py TIMELINE) ----
    // NOTE: this initial block must track N_EVENTS; update entries if N_EVENTS changes.
    reg [15:0] ev_rom [0:N_EVENTS-1];
    reg [63:0] da_rom [0:N_EVENTS-1];
    initial begin
        ev_rom[0] = 16'h0001; da_rom[0] = 64'h1111222233334444;
        ev_rom[1] = 16'h00A5; da_rom[1] = 64'hAAAABBBBCCCCDDDD;
        ev_rom[2] = 16'h1000; da_rom[2] = 64'h0123456789ABCDEF;
    end

    localparam [7:0] COMMA = 8'hBC;

    localparam [0:0] S_WARMUP = 1'b0;
    localparam [0:0] S_RUN    = 1'b1;
    reg st;

    // nxt_idx: the event index whose CRC is currently being computed
    reg [$clog2(N_EVENTS)-1:0] nxt_idx;

    // CRC interface
    reg        crc_calc;
    reg [79:0] crc_data;
    wire [7:0] crc_q;
    wire       crc_valid;

    // Gearbox interface
    reg [95:0] data96;
    reg        data96_valid;

    // Emission counter 0..5 (steady-state loop), warmup counter 0..4
    reg [2:0]  emit_ctr;
    reg [2:0]  warm_ctr;

    // Next frame's 96-bit word latched at emit_ctr=3 (CRC_VALID cycle)
    reg [95:0] next_data96;

    CRC8_CALC u_crc (
        .CLK(CLK1), .RESETn(RESETn), .CALC(crc_calc),
        .DATA({crc_data, 8'h00}), .CRC(crc_q), .CRC_VALID(crc_valid));

    GEARBOX_96_TO_16 u_gb (
        .CLK1(CLK1), .RESETn(RESETn),
        .DATA96(data96), .K_IN(12'b1000_0000_0000),
        .DATA96_VALID(data96_valid), .DATA16(DATA16), .K_OUT(K_OUT));

    // Wrap-around next index
    function automatic [$clog2(N_EVENTS)-1:0] next_of;
        input [$clog2(N_EVENTS)-1:0] i;
        begin
            next_of = (i == N_EVENTS - 1) ? {$clog2(N_EVENTS){1'b0}} : i + 1'b1;
        end
    endfunction

    always @(posedge CLK1 or negedge RESETn) begin
        if (!RESETn) begin
            st           <= S_WARMUP;
            nxt_idx      <= {$clog2(N_EVENTS){1'b0}};
            crc_calc     <= 1'b0;
            crc_data     <= 80'd0;
            data96       <= 96'd0;
            data96_valid <= 1'b0;
            emit_ctr     <= 3'd0;
            warm_ctr     <= 3'd0;
            next_data96  <= 96'd0;
            MARKER       <= 1'b0;
        end else begin
            crc_calc     <= 1'b0;
            data96_valid <= 1'b0;
            MARKER       <= 1'b0;

            case (st)
                // -------------------------------------------------------------------
                // S_WARMUP: prime the CRC pipeline for frame0 and fire the first
                // DATA96_VALID.  Five cycles (warm_ctr 0..4).
                //
                //  ctr=0: present frame0 to CRC; set crc_calc. CRC8_CALC sees
                //         CALC=1 at ctr=1 (because crc_calc is registered).
                //  ctr=1,2,3: pipeline bubbles (calc0, calc1, CRC latch).
                //  ctr=4: CRC_VALID=1 for frame0. Fire DATA96_VALID. Also set
                //         crc_calc for frame1 so CRC8_CALC sees CALC=1 at
                //         emit_ctr=0 (the gearbox-registration cycle), giving
                //         CRC_VALID at emit_ctr=3. Enter S_RUN.
                // -------------------------------------------------------------------
                S_WARMUP: begin
                    warm_ctr <= warm_ctr + 3'd1;
                    if (warm_ctr == 3'd0) begin
                        crc_data <= {ev_rom[nxt_idx], da_rom[nxt_idx]};
                        crc_calc <= 1'b1;
                    end
                    if (warm_ctr == 3'd4) begin
                        // CRC_VALID for frame0 is high now.
                        data96       <= {COMMA, {ev_rom[nxt_idx], da_rom[nxt_idx]}, crc_q};
                        data96_valid <= 1'b1;
                        MARKER       <= 1'b1;
                        // Immediately prime frame1's CRC: CRC8_CALC will see
                        // CALC=1 at the next posedge (emit_ctr=0).
                        nxt_idx  <= next_of(nxt_idx);
                        crc_data <= {ev_rom[next_of(nxt_idx)], da_rom[next_of(nxt_idx)]};
                        crc_calc <= 1'b1;
                        emit_ctr <= 3'd0;
                        st       <= S_RUN;
                    end
                end

                // -------------------------------------------------------------------
                // S_RUN: steady-state gapless loop, 6 cycles per frame.
                //
                //  ctr=0 (G): Gearbox latches frame. CRC8_CALC just saw CALC=1
                //             (set at previous ctr=5 or at warm_ctr=4). Pipeline
                //             started; calc0=1 after this posedge.
                //  ctr=1: word0 visible. calc1=1.
                //  ctr=2: word1 visible. CRC output latching.
                //  ctr=3: word2 visible. CRC_VALID=1. Latch next_data96. Advance
                //         nxt_idx. (Do NOT start the next CALC here -- it is
                //         started at ctr=5.)
                //  ctr=4: word3 visible.
                //  ctr=5: word4 visible. Fire DATA96_VALID for next frame (gearbox
                //         sees it at G+6 = word5 cycle, outputting word5 while
                //         simultaneously latching new frame). Start CALC for the
                //         frame after next (CRC8_CALC sees CALC=1 at the new
                //         frame's ctr=0, giving CRC_VALID at its ctr=3).
                //  [G+6]: word5 visible; gearbox latches new frame. emit_ctr=0.
                // -------------------------------------------------------------------
                S_RUN: begin
                    emit_ctr <= emit_ctr + 3'd1;

                    if (emit_ctr == 3'd3) begin
                        // CRC_VALID for nxt_idx is asserted this cycle.
                        next_data96 <= {COMMA,
                                        {ev_rom[nxt_idx], da_rom[nxt_idx]},
                                        crc_q};
                        nxt_idx <= next_of(nxt_idx);
                    end

                    if (emit_ctr == 3'd5) begin
                        // Drive next frame into the gearbox.
                        data96       <= next_data96;
                        data96_valid <= 1'b1;
                        MARKER       <= 1'b1;
                        // Start CRC for the frame after nxt_idx (already updated
                        // at ctr=3). CRC8_CALC sees CALC at the next posedge
                        // (new frame ctr=0), delivering CRC_VALID at new ctr=3.
                        crc_data <= {ev_rom[nxt_idx], da_rom[nxt_idx]};
                        crc_calc <= 1'b1;
                        emit_ctr <= 3'd0;
                    end
                end

                default: st <= S_WARMUP;
            endcase
        end
    end
endmodule
