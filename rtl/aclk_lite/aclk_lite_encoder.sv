// rtl/aclk_lite/aclk_lite_encoder.sv
//
// Real-framing ACLK-Lite / TCLK biphase-mark ENCODER. A free-running cell engine:
// it ALWAYS emits 100 ns biphase-mark cells so the receiver's serdec keeps carrier
// lock. Idle = continuous logical-1 cells. On `start` (while idle) it serializes one
// frame - bytes assembled from event_id/data per frame_type, each byte = start(0) +
// 8 data MSB-first + even parity, bytes back-to-back - then returns to idle 1-cells.
// Biphase-mark cell: a transition at the cell boundary, plus an extra mid-cell
// transition iff the cell bit is 1. SAMPLES_PER_CELL clk cycles per cell (HALF each
// half). The emitted line is bit-identical to tb/tclk_tx_model.biphase_samples.
// frame_type: 0 = TCLK (1 byte), 1 = ACLK event (2 bytes), 2 = full packet
// (12 bytes: event 0-1, data 2-9, CRC 10 = 0x00, control 11 = 0x00). A start while
// busy is ignored.

`timescale 1ns / 1ps

module aclk_lite_encoder #(
    parameter int SAMPLES_PER_CELL = 8       // oversampling-clock cycles per 100 ns cell
) (
    input  logic        clk,
    input  logic        rstn,                // async, active-low
    input  logic        start,               // 1-cycle strobe; ignored while busy
    input  logic [15:0] event_id,
    input  logic [63:0] data,
    input  logic [1:0]  frame_type,          // 0=TCLK 1B, 1=ACLK event 2B, 2=full 12B
    output logic        line,                // idle = continuous 1-cells
    output logic        busy
);

    localparam int HALF = SAMPLES_PER_CELL / 2;

    // ---- combinational byte assembly ----
    logic [7:0]  byte_arr [0:11];
    logic [7:0]  nbytes;
    integer      bi;
    always_comb begin
        for (bi = 0; bi < 12; bi = bi + 1) byte_arr[bi] = 8'h00;
        case (frame_type)
            2'd0: begin
                nbytes      = 8'd1;
                byte_arr[0] = event_id[7:0];
            end
            2'd1: begin
                nbytes      = 8'd2;
                byte_arr[0] = event_id[15:8];
                byte_arr[1] = event_id[7:0];
            end
            default: begin                    // 2 = full 12-byte packet
                nbytes       = 8'd12;
                byte_arr[0]  = event_id[15:8];
                byte_arr[1]  = event_id[7:0];
                byte_arr[2]  = data[63:56];
                byte_arr[3]  = data[55:48];
                byte_arr[4]  = data[47:40];
                byte_arr[5]  = data[39:32];
                byte_arr[6]  = data[31:24];
                byte_arr[7]  = data[23:16];
                byte_arr[8]  = data[15:8];
                byte_arr[9]  = data[7:0];
                byte_arr[10] = 8'h00;         // CRC placeholder
                byte_arr[11] = 8'h00;         // control placeholder
            end
        endcase
    end

    // ---- frame-bit vector, MSB-first, left-aligned at [119] ----
    // per byte = start(0) + 8 data (MSB first) + even parity (XOR of data bits).
    logic [119:0] framebits_n;
    logic [7:0]   nbits_n;
    integer       kk;
    always_comb begin
        framebits_n = 120'd0;
        for (kk = 0; kk < 12; kk = kk + 1) begin
            if (kk < nbytes)
                framebits_n[119 - kk*10 -: 10] = {1'b0, byte_arr[kk], ^byte_arr[kk]};
        end
        nbits_n = nbytes * 8'd10;
    end

    // ---- free-running biphase-mark cell engine ----
    logic [119:0] framebits;
    logic [7:0]   nbits;
    logic [7:0]   bit_idx;       // current cell, 0..nbits-1 while busy
    logic [3:0]   cnt;           // sample within the cell, 0..SAMPLES_PER_CELL-1
    logic         level;         // current line level
    logic         pending;       // a start seen while idle; begin at next cell boundary

    // current cell bit: idle -> 1, busy -> the frame bit (MSB-first)
    wire cur_bit = busy ? framebits[8'd119 - bit_idx] : 1'b1;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            framebits <= 120'd0;
            nbits     <= 8'd0;
            bit_idx   <= 8'd0;
            cnt       <= 4'd0;
            level     <= 1'b1;
            line      <= 1'b1;
            busy      <= 1'b0;
            pending   <= 1'b0;
        end else begin
            // latch a start while idle (consumed at the next cell boundary)
            if (start && !busy) pending <= 1'b1;

            // biphase-mark transitions within the cell
            if (cnt == 4'd0) begin
                level <= ~level;              // boundary transition (every cell)
                line  <= ~level;
            end else if (cnt == HALF[3:0]) begin
                if (cur_bit) begin
                    level <= ~level;          // mid-cell transition for a 1
                    line  <= ~level;
                end
            end

            // advance the cell / sequence at the end of each cell
            if (cnt == SAMPLES_PER_CELL[3:0] - 4'd1) begin
                cnt <= 4'd0;
                if (busy) begin
                    if (bit_idx == nbits - 8'd1) begin
                        busy    <= 1'b0;      // frame done -> back to idle 1-cells
                        bit_idx <= 8'd0;
                    end else begin
                        bit_idx <= bit_idx + 8'd1;
                    end
                end else if (pending) begin
                    busy      <= 1'b1;
                    pending   <= 1'b0;
                    framebits <= framebits_n;
                    nbits     <= nbits_n;
                    bit_idx   <= 8'd0;
                end
            end else begin
                cnt <= cnt + 4'd1;
            end
        end
    end

endmodule
