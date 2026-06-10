// rtl/async_fifo.sv
//
// Dual-clock (asynchronous) FIFO with Gray-coded pointers and first-word
// fall-through (FWFT). It safely carries data from a write clock domain to an
// independent read clock domain, which is exactly what the ACLK readout needs:
// ACLK_RCV runs on the recovered RX clock, while the PS-facing AXI logic runs
// on pl_clk0, and the two are asynchronous of eachother.
//
// Design (the standard Cummings two-clock FIFO):
//   - Each side keeps a binary pointer with one extra MSB for full/empty.
//   - Pointers cross domains in Gray code (one bit changes per step), through a
//     2-flop synchronizer (rtl/synchronizer.sv), so a sample taken mid-flight
//     can only be the old or the new value, never a corrupt in-between code.
//   - empty: the read-side Gray pointer equals the synchronized write pointer.
//   - full:  the next write-side Gray pointer equals the synchronized read
//            pointer with its top two bits inverted.
//   - FWFT: while empty is low, rd_data already shows the oldest entry. Pulse
//     rd_en for one rd_clk to pop it.
//   - overflow is sticky: it latches high if a write is attempted while full
//     (a dropped entry) and stays set until wr_rstn. The readout uses this as
//     its "we lost an event" alarm.
//
// Resets are asynchronous, active-low, one per domain (matching both the
// aclk_bridge RESETn convention and AXI's active-low ARESETn).
//
// DEPTH is 2**ADDR_WIDTH (a power of two, required by the Gray-code scheme).

`timescale 1ns / 1ps

module async_fifo #(
    parameter int WIDTH      = 96,
    parameter int ADDR_WIDTH = 6            // DEPTH = 2**ADDR_WIDTH
) (
    // ---- write domain ----
    input  logic              wr_clk,
    input  logic              wr_rstn,      // async, active-low
    input  logic              wr_en,
    input  logic [WIDTH-1:0]  wr_data,
    output logic              full,
    output logic              overflow,     // sticky: a write was dropped while full

    // ---- read domain ----
    input  logic              rd_clk,
    input  logic              rd_rstn,      // async, active-low
    input  logic              rd_en,
    output logic [WIDTH-1:0]  rd_data,
    output logic              empty
);

    localparam int DEPTH = 1 << ADDR_WIDTH;

    // Storage. Written in wr_clk, read combinationally in rd_clk (FWFT).
    logic [WIDTH-1:0] mem [DEPTH];

    // Binary and Gray pointers, each ADDR_WIDTH+1 bits (the extra MSB is the
    // wrap bit that lets full and empty be told apart at the same address).
    logic [ADDR_WIDTH:0] wr_bin, wr_gray;
    logic [ADDR_WIDTH:0] rd_bin, rd_gray;

    // Pointers brought across the clock boundary.
    logic [ADDR_WIDTH:0] wr_gray_sync_rd;   // write pointer seen in the read domain
    logic [ADDR_WIDTH:0] rd_gray_sync_wr;   // read pointer seen in the write domain

    // ---------------------------------------------------------------
    // Write pointer
    // ---------------------------------------------------------------
    wire                do_wr       = wr_en && !full;
    wire [ADDR_WIDTH:0] wr_bin_nxt  = wr_bin + (do_wr ? 1'b1 : 1'b0);
    wire [ADDR_WIDTH:0] wr_gray_nxt = (wr_bin_nxt >> 1) ^ wr_bin_nxt;

    always_ff @(posedge wr_clk or negedge wr_rstn) begin
        if (!wr_rstn) begin
            wr_bin  <= '0;
            wr_gray <= '0;
        end else begin
            if (do_wr)
                mem[wr_bin[ADDR_WIDTH-1:0]] <= wr_data;
            wr_bin  <= wr_bin_nxt;
            wr_gray <= wr_gray_nxt;
        end
    end

    // full: the next write Gray equals the read Gray with the top two bits flipped.
    wire full_nxt = (wr_gray_nxt ==
        {~rd_gray_sync_wr[ADDR_WIDTH:ADDR_WIDTH-1],
          rd_gray_sync_wr[ADDR_WIDTH-2:0]});

    always_ff @(posedge wr_clk or negedge wr_rstn) begin
        if (!wr_rstn) full <= 1'b0;
        else          full <= full_nxt;
    end

    // sticky overflow: a write requested while full is a dropped entry.
    always_ff @(posedge wr_clk or negedge wr_rstn) begin
        if (!wr_rstn)            overflow <= 1'b0;
        else if (wr_en && full)  overflow <= 1'b1;
    end

    // ---------------------------------------------------------------
    // Read pointer
    // ---------------------------------------------------------------
    wire                do_rd       = rd_en && !empty;
    wire [ADDR_WIDTH:0] rd_bin_nxt  = rd_bin + (do_rd ? 1'b1 : 1'b0);
    wire [ADDR_WIDTH:0] rd_gray_nxt = (rd_bin_nxt >> 1) ^ rd_bin_nxt;

    always_ff @(posedge rd_clk or negedge rd_rstn) begin
        if (!rd_rstn) begin
            rd_bin  <= '0;
            rd_gray <= '0;
        end else begin
            rd_bin  <= rd_bin_nxt;
            rd_gray <= rd_gray_nxt;
        end
    end

    // empty: the read Gray has caught up with the synchronized write Gray.
    always_ff @(posedge rd_clk or negedge rd_rstn) begin
        if (!rd_rstn) empty <= 1'b1;
        else          empty <= (rd_gray_nxt == wr_gray_sync_rd);
    end

    assign rd_data = mem[rd_bin[ADDR_WIDTH-1:0]];   // FWFT: head is always exposed

    // ---------------------------------------------------------------
    // Pointer clock-domain crossings (reuse the project CDC primitive)
    // ---------------------------------------------------------------
    synchronizer #(.WIDTH(ADDR_WIDTH+1), .STAGES(2)) u_sync_wr2rd (
        .clk          (rd_clk),
        .async_signal (wr_gray),
        .sync_signal  (wr_gray_sync_rd)
    );

    synchronizer #(.WIDTH(ADDR_WIDTH+1), .STAGES(2)) u_sync_rd2wr (
        .clk          (wr_clk),
        .async_signal (rd_gray),
        .sync_signal  (rd_gray_sync_wr)
    );

endmodule
