// rtl/aclk_lite/aclk_lite_gen_timeline.sv
//
// Hardcoded ACLK-Lite event source: drives aclk_lite_encoder through a fixed trio
// of frames forever, with idle gaps between them, and pulses frame_sync at the
// start of each trio (a clean scope trigger). No PS/AXI interaction: it boots and
// transmits. The trio exercises all three decoder paths:
//   frame0: 8-bit  TCLK event id 0x55
//   frame1: 16-bit ACLK event id 0xABCD
//   frame2: 80-bit ACLK event id 0x1234 + data 0xDEADBEEFCAFE0001
//
// IDLE_GAP must exceed the decoder's ~1.5-bit frame-end gap (OVERSAMPLE*3/2); the
// default 64 clears it comfortably. TRIO_GAP defaults to ~1 ms at 120 MHz.

`timescale 1ns / 1ps

module aclk_lite_gen_timeline #(
    parameter int OVERSAMPLE = 12,
    parameter int IDLE_GAP   = 64,         // idle clk cycles between frames
    parameter int TRIO_GAP   = 120000      // idle clk cycles before repeating (~1 ms)
) (
    input  logic clk,
    input  logic rstn,
    output logic line,                     // Manchester output (idle high)
    output logic frame_sync                // 1-cycle pulse at the start of each trio
);

    localparam logic [79:0] PL0 = 80'h00000000_00000000_0055;            // 8-bit  id 0x55
    localparam logic [79:0] PL1 = 80'h00000000_00000000_ABCD;            // 16-bit id 0xABCD
    localparam logic [79:0] PL2 = {16'h1234, 64'hDEADBEEF_CAFE_0001};    // 80-bit
    localparam logic [6:0]  LEN0 = 7'd8;
    localparam logic [6:0]  LEN1 = 7'd16;
    localparam logic [6:0]  LEN2 = 7'd80;

    logic        enc_start;
    logic [79:0] enc_payload;
    logic [6:0]  enc_length;
    logic        enc_busy;

    aclk_lite_encoder #(.OVERSAMPLE(OVERSAMPLE)) u_enc (
        .clk(clk), .rstn(rstn),
        .start(enc_start), .payload(enc_payload), .length(enc_length),
        .line(line), .busy(enc_busy)
    );

    typedef enum logic [2:0] {
        S_SYNC, S_F0, S_W0, S_F1, S_W1, S_F2, S_W2
    } state_t;
    state_t      state;
    logic [31:0] gap_cnt;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state       <= S_SYNC;
            gap_cnt     <= 32'd0;
            enc_start   <= 1'b0;
            enc_payload <= 80'd0;
            enc_length  <= 7'd0;
            frame_sync  <= 1'b0;
        end else begin
            enc_start  <= 1'b0;          // default: 1-cycle strobe
            frame_sync <= 1'b0;
            case (state)
                S_SYNC: begin
                    frame_sync  <= 1'b1;
                    enc_payload <= PL0; enc_length <= LEN0; enc_start <= 1'b1;
                    gap_cnt     <= 32'd0;
                    state       <= S_F0;
                end
                S_F0: if (enc_busy) state <= S_W0;     // wait for the encoder to take it
                S_W0: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_payload <= PL1; enc_length <= LEN1; enc_start <= 1'b1;
                              state <= S_F1;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                S_F1: if (enc_busy) state <= S_W1;
                S_W1: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_payload <= PL2; enc_length <= LEN2; enc_start <= 1'b1;
                              state <= S_F2;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                S_F2: if (enc_busy) state <= S_W2;
                S_W2: if (!enc_busy) begin
                          if (gap_cnt == TRIO_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              state <= S_SYNC;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                default: state <= S_SYNC;
            endcase
        end
    end

endmodule
