// rtl/aclk_lite/aclk_lite_decoder.sv
//
// ACLK-Lite Decoder Module (ADM): recovers a Manchester-encoded ACLK-Lite stream
// (oversampled by the PL clock) into parallel events. This is the Manchester
// front-end the KR260 uses; it is a different block than the GT-based ACLK_RCV
// in rtl/aclk_bridge. Its event/data outputs feed the readout (aclk_readout_*).
//
// Length-aware decode (per the ACLK-Lite spec):
//   - 8 payload bits  -> legacy TCLK event: event_id = {8'h00, payload[7:0]}
//   - 16 payload bits -> ACLK event:        event_id = payload[15:0]
//   - 80 payload bits -> ACLK event + data: event_id = payload[79:64],
//                                            data     = payload[63:0]
//
// Encoding implemented (clean and self-consistent; reconcile with the official
// ACLK-Lite spec before trusting a real external source):
//   - Standard Manchester per bit: b -> two half-bits {~b, b} (first half ~b,
//     second half b), OVERSAMPLE clk cycles per bit.
//   - Idle = line steady HIGH (a deliberate Manchester violation) = the frame
//     delimiter.
//   - Frame = start bit (0) + payload (MSB first) + parity (even = XOR payload),
//     then back to idle.
//
// Recovery: 2FF-synchronize the line, then sample the bit value at each mid-bit
// edge. An edge is a mid-bit edge when the gap since the last accepted edge is at
// least ~0.75 of a bit; shorter edges (the ~0.5-bit boundary transitions between
// equal bits) are ignored. Frame end is no mid-bit edge for ~1.5 bits.

`timescale 1ns / 1ps

module aclk_lite_decoder #(
    parameter int OVERSAMPLE = 16          // oversampling-clock cycles per Manchester bit
) (
    input  logic        clk,               // oversampling clock
    input  logic        rstn,              // async, active-low
    input  logic        line,              // Manchester serial input (async)

    output logic        event_valid,       // 1-cycle strobe: an event was decoded
    output logic [15:0] event_id,
    output logic        data_valid,        // 1-cycle strobe: a 64-bit payload came with it
    output logic [63:0] data,
    output logic        parity_error,      // 1-cycle strobe: bad parity or malformed length
    output logic        is_tclk            // 1-cycle strobe with event_valid: 8-bit (TCLK) event
);

    localparam int MidGap  = (OVERSAMPLE * 3) / 4;    // accept a mid-bit edge past ~0.75 bit
    localparam int IdleGap = (OVERSAMPLE * 3) / 2;    // ~1.5 bit with no edge -> frame end

    localparam logic StIdle   = 1'b0;
    localparam logic StActive = 1'b1;

    // Synchronize the asynchronous line into clk (reuse the project CDC primitive).
    logic line_s, line_d;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync (
        .clk(clk), .async_signal(line), .sync_signal(line_s)
    );
    always_ff @(posedge clk or negedge rstn)
        if (!rstn) line_d <= 1'b1;
        else       line_d <= line_s;

    wire rising  =  line_s & ~line_d;
    wire falling = ~line_s &  line_d;
    wire any_edge = rising | falling;

    logic        state;
    logic [7:0]  gap;        // clk cycles since the last accepted mid-bit edge
    logic [7:0]  cnt;        // captured bits this frame (start + payload + parity)
    logic [81:0] sr;         // captured bits, newest in LSB

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            state        <= StIdle;
            gap          <= 8'd0;
            cnt          <= 8'd0;
            sr           <= 82'd0;
            event_valid  <= 1'b0;
            event_id     <= 16'd0;
            data_valid   <= 1'b0;
            data         <= 64'd0;
            parity_error <= 1'b0;
            is_tclk      <= 1'b0;
        end else begin
            event_valid  <= 1'b0;          // default: outputs are 1-cycle strobes
            data_valid   <= 1'b0;
            parity_error <= 1'b0;
            is_tclk      <= 1'b0;

            case (state)
                StIdle: begin
                    if (falling) begin
                        // Idle is high; the start bit (0) gives the first mid-bit
                        // (falling) edge. Capture it as the first bit.
                        sr    <= {81'd0, line_s};   // line_s == 0 here
                        cnt   <= 8'd1;
                        gap   <= 8'd0;
                        state <= StActive;
                    end
                end

                StActive: begin
                    gap <= gap + 8'd1;
                    if (any_edge && (gap >= MidGap[7:0])) begin
                        // mid-bit edge: the post-edge level is this bit's value
                        sr  <= {sr[80:0], line_s};
                        cnt <= cnt + 8'd1;
                        gap <= 8'd0;
                    end else if (gap >= IdleGap[7:0]) begin
                        // no mid-bit edge for ~1.5 bits: frame is complete
                        state <= StIdle;
                        case (cnt)
                            8'd10: begin               // start + 8 payload + parity
                                if (^sr[8:1] == sr[0]) begin
                                    event_id    <= {8'h00, sr[8:1]};
                                    event_valid <= 1'b1;
                                    is_tclk      <= 1'b1;   // legacy 8-bit TCLK event
                                end else
                                    parity_error <= 1'b1;
                            end
                            8'd18: begin               // start + 16 payload + parity
                                if (^sr[16:1] == sr[0]) begin
                                    event_id    <= sr[16:1];
                                    event_valid <= 1'b1;
                                end else
                                    parity_error <= 1'b1;
                            end
                            8'd82: begin               // start + 80 payload + parity
                                if (^sr[80:1] == sr[0]) begin
                                    event_id    <= sr[80:65];
                                    data        <= sr[64:1];
                                    event_valid <= 1'b1;
                                    data_valid  <= 1'b1;
                                end else
                                    parity_error <= 1'b1;
                            end
                            default: parity_error <= 1'b1;   // malformed length
                        endcase
                    end
                end

                default: state <= StIdle;
            endcase
        end
    end

endmodule
