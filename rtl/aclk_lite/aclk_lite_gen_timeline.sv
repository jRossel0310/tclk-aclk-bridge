// rtl/aclk_lite/aclk_lite_gen_timeline.sv
//
// Hardcoded real-framing ACLK-Lite event source: drives aclk_lite_encoder through a
// fixed trio of frames forever, with idle gaps between them, and pulses frame_sync at
// the start of each trio (a clean scope trigger). No PS/AXI: it boots and transmits.
// The trio exercises all three decoder paths:
//   frame0: TCLK event 0x55          (frame_type 0, 1 byte)
//   frame1: ACLK event 0xABCD        (frame_type 1, 2 bytes)
//   frame2: ACLK event 0x1234 + data 0xDEADBEEFCAFE0001  (frame_type 2, 12 bytes)
//
// The encoder free-runs the carrier, so an "idle gap" is the encoder emitting idle
// 1-cells between frames. IDLE_GAP must exceed the 2 terminal idle cells the framer
// keys on (2 cells = 16 clks at SAMPLES_PER_CELL=8); the default 64 clears it.
// TRIO_GAP defaults to ~1 ms at 80 MHz.
//
// S_WARM: on power-up the serdec in the receiver needs a brief carrier warm-up before
// it can lock; S_WARM idles for TRIO_GAP cycles (the encoder emits 1-cells) before the
// first trio so the serdec is fully settled.

`timescale 1ns / 1ps

module aclk_lite_gen_timeline #(
    parameter int SAMPLES_PER_CELL = 8,
    parameter int IDLE_GAP = 64,           // idle clk cycles between frames
    parameter int TRIO_GAP = 80000         // idle clk cycles before repeating (~1 ms)
) (
    input  logic clk,
    input  logic rstn,
    output logic line,                     // biphase-mark output (idle = 1-cells)
    output logic frame_sync                // 1-cycle pulse at the start of each trio
);

    localparam logic [15:0] EV0 = 16'h0055;  localparam logic [1:0] FT0 = 2'd0;
    localparam logic [15:0] EV1 = 16'hABCD;  localparam logic [1:0] FT1 = 2'd1;
    localparam logic [15:0] EV2 = 16'h1234;  localparam logic [1:0] FT2 = 2'd2;
    localparam logic [63:0] DAT2 = 64'hDEADBEEF_CAFE_0001;

    logic        enc_start;
    logic [15:0] enc_event;
    logic [63:0] enc_data;
    logic [1:0]  enc_ftype;
    logic        enc_busy;

    aclk_lite_encoder #(.SAMPLES_PER_CELL(SAMPLES_PER_CELL)) u_enc (
        .clk(clk), .rstn(rstn),
        .start(enc_start), .event_id(enc_event), .data(enc_data),
        .frame_type(enc_ftype), .line(line), .busy(enc_busy)
    );

    typedef enum logic [2:0] {
        S_WARM, S_SYNC, S_F0, S_W0, S_F1, S_W1, S_F2, S_W2
    } state_t;
    state_t      state;
    logic [31:0] gap_cnt;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state     <= S_WARM;
            gap_cnt   <= 32'd0;
            enc_start <= 1'b0;
            enc_event <= 16'd0;
            enc_data  <= 64'd0;
            enc_ftype <= 2'd0;
            frame_sync<= 1'b0;
        end else begin
            enc_start  <= 1'b0;          // default: 1-cycle strobe
            frame_sync <= 1'b0;
            case (state)
                S_WARM: begin
                    if (gap_cnt == TRIO_GAP - 1) begin
                        gap_cnt <= 32'd0;
                        state   <= S_SYNC;
                    end else gap_cnt <= gap_cnt + 32'd1;
                end
                S_SYNC: begin
                    frame_sync <= 1'b1;
                    enc_event  <= EV0; enc_data <= 64'd0; enc_ftype <= FT0; enc_start <= 1'b1;
                    gap_cnt    <= 32'd0;
                    state      <= S_F0;
                end
                S_F0: if (enc_busy) state <= S_W0;
                S_W0: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_event <= EV1; enc_data <= 64'd0;
                              enc_ftype <= FT1; enc_start <= 1'b1;
                              state <= S_F1;
                          end else gap_cnt <= gap_cnt + 32'd1;
                      end
                S_F1: if (enc_busy) state <= S_W1;
                S_W1: if (!enc_busy) begin
                          if (gap_cnt == IDLE_GAP - 1) begin
                              gap_cnt <= 32'd0;
                              enc_event <= EV2; enc_data <= DAT2;
                              enc_ftype <= FT2; enc_start <= 1'b1;
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
                default: state <= S_WARM;
            endcase
        end
    end

endmodule
