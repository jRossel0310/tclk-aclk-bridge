// rtl/aclk_lite/aclk_lite_encoder.sv
//
// ACLK-Lite Manchester ENCODER: the TX counterpart of aclk_lite_decoder.sv. Given
// a start strobe + payload + length, it serializes one frame onto `line` and then
// returns to idle high. The emitted per-clk waveform is bit-identical to the golden
// model tb/manchester_tx_model.py frame_levels(payload, length).
//
// Encoding (matches the decoder): bit b -> two half-bits {~b, b}, OVERSAMPLE clk
// cycles per FULL bit (HALF each half); idle = steady high; frame = start(0) +
// payload MSB-first + even parity (XOR of payload), then idle high.
//
// Contract: payload is right-justified and its unused upper bits MUST be 0.
// length must be one of 8, 16, 80. A start pulse while busy is ignored.

`timescale 1ns / 1ps

module aclk_lite_encoder #(
    parameter int OVERSAMPLE = 12               // oversampling-clock cycles per bit
) (
    input  logic        clk,
    input  logic        rstn,                   // async, active-low
    input  logic        start,                  // 1-cycle strobe; ignored while busy
    input  logic [79:0] payload,                // right-justified; unused upper bits 0
    input  logic [6:0]  length,                 // 8, 16, or 80
    output logic        line,                   // idle HIGH
    output logic        busy
);

    localparam int HALF = OVERSAMPLE / 2;

    localparam logic StIdle = 1'b0;
    localparam logic StSend = 1'b1;

    logic        state;
    logic [81:0] sr;        // bits to send, MSB-first; top bit (sr[81]) = start bit
    logic [7:0]  nbits;     // total bits in this frame (length + 2)
    logic [7:0]  bit_idx;   // bit currently being sent, 0..nbits-1
    logic [7:0]  half_cnt;  // half-bit cycle counter, 0..HALF
    logic        phase;     // 0 = first half (~b), 1 = second half (b)

    wire cur_bit  = sr[8'd81 - bit_idx];
    wire next_bit = sr[8'd81 - (bit_idx + 8'd1)];

    // Combinationally assemble the left-aligned frame + even parity for `length`.
    logic        par;
    logic [81:0] sr_n;
    logic [7:0]  nbits_n;
    always_comb begin
        case (length)
            7'd8: begin
                par     = ^payload[7:0];
                sr_n    = {1'b0, payload[7:0],  par, 72'd0};
                nbits_n = 8'd10;
            end
            7'd16: begin
                par     = ^payload[15:0];
                sr_n    = {1'b0, payload[15:0], par, 64'd0};
                nbits_n = 8'd18;
            end
            default: begin   // 80
                par     = ^payload[79:0];
                sr_n    = {1'b0, payload[79:0], par};
                nbits_n = 8'd82;
            end
        endcase
    end

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state    <= StIdle;
            line     <= 1'b1;
            busy     <= 1'b0;
            sr       <= 82'd0;
            nbits    <= 8'd0;
            bit_idx  <= 8'd0;
            half_cnt <= 8'd0;
            phase    <= 1'b0;
        end else begin
            case (state)
                StIdle: begin
                    line <= 1'b1;
                    busy <= 1'b0;
                    if (start) begin
                        sr       <= sr_n;
                        nbits    <= nbits_n;
                        bit_idx  <= 8'd0;
                        half_cnt <= 8'd0;
                        phase    <= 1'b0;
                        busy     <= 1'b1;
                        line     <= ~sr_n[81];   // first half of start bit (~0 = 1)
                        state    <= StSend;
                    end
                end

                StSend: begin
                    if (half_cnt == HALF[7:0]) begin
                        half_cnt <= 8'd1;
                        if (phase == 1'b0) begin
                            phase <= 1'b1;
                            line  <= cur_bit;            // second half of current bit
                        end else begin
                            phase <= 1'b0;
                            if (bit_idx == nbits - 8'd1) begin
                                state <= StIdle;
                                busy  <= 1'b0;
                                line  <= 1'b1;           // back to idle high
                            end else begin
                                bit_idx <= bit_idx + 8'd1;
                                line    <= ~next_bit;    // first half of next bit
                            end
                        end
                    end else begin
                        half_cnt <= half_cnt + 8'd1;
                    end
                end

                default: state <= StIdle;
            endcase
        end
    end

endmodule
