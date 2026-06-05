// rtl/fifo.sv
//
// Synchronous (single-clock) FIFO with first-word fall-through (FWFT):
//   - When `empty` is low, `dout` already shows the oldest entry — no read
//     latency. Pulse `rd_en` for one cycle to pop it; `dout` then advances to
//     the next entry on the following clock.
//   - A write is accepted on any cycle where `wr_en` is high and `full` is low.
//   - Simultaneous read+write while non-empty/non-full is supported and leaves
//     the occupancy unchanged.
//
// DEPTH need not be a power of two (pointers wrap explicitly). Occupancy is
// tracked with a count register, so `full`/`empty` are exact.

`timescale 1ns / 1ps

module fifo #(
    parameter int WIDTH = 8,
    parameter int DEPTH = 32
) (
    input  logic             clk,
    input  logic             rst,    // synchronous, active-high

    // Write side
    input  logic             wr_en,
    input  logic [WIDTH-1:0] din,
    output logic             full,

    // Read side
    input  logic             rd_en,
    output logic [WIDTH-1:0] dout,
    output logic             empty
);

    localparam int PTR_WIDTH = (DEPTH > 1) ? $clog2(DEPTH) : 1;

    logic [WIDTH-1:0]     mem [DEPTH];
    logic [PTR_WIDTH-1:0] wr_ptr, rd_ptr;
    logic [PTR_WIDTH:0]   count;            // 0 .. DEPTH inclusive

    assign full  = (count == DEPTH);   // count is PTR_WIDTH+1 bits, so DEPTH fits
    assign empty = (count == '0);
    assign dout  = mem[rd_ptr];             // first-word fall-through

    wire do_wr = wr_en && !full;
    wire do_rd = rd_en && !empty;

    always_ff @(posedge clk) begin
        if (rst) begin
            wr_ptr <= '0;
            rd_ptr <= '0;
            count  <= '0;
        end else begin
            if (do_wr) begin
                mem[wr_ptr] <= din;
                wr_ptr <= (wr_ptr == PTR_WIDTH'(DEPTH - 1)) ? '0 : wr_ptr + 1'b1;
            end
            if (do_rd) begin
                rd_ptr <= (rd_ptr == PTR_WIDTH'(DEPTH - 1)) ? '0 : rd_ptr + 1'b1;
            end
            // Occupancy only changes when exactly one of read/write happens.
            unique case ({do_wr, do_rd})
                2'b10:   count <= count + 1'b1;
                2'b01:   count <= count - 1'b1;
                default: count <= count;
            endcase
        end
    end

endmodule
