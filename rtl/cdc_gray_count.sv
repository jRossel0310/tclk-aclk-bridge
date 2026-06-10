// rtl/cdc_gray_count.sv
//
// A monotonic event counter whose value is safely readable in a different clock
// domain. The counter increments by at most 1 per src_clk, so its Gray code
// changes one bit at a time; that Gray value crosses to dst_clk through the
// project synchronizer (rtl/synchronizer.sv) and is converted back to binary on
// the destination side.
//
// Used for the readout's diagnostic counters (events, nulls, errors), which are
// incremented in the recovered-RX domain but read over AXI on the PS clock.

`timescale 1ns / 1ps

module cdc_gray_count #(
    parameter int W = 32
) (
    input  logic         src_clk,
    input  logic         src_rstn,    // async, active-low
    input  logic         incr,        // pulse, at most one per src_clk
    input  logic         dst_clk,
    output logic [W-1:0] count_dst
);

    logic [W-1:0] bin;
    always_ff @(posedge src_clk or negedge src_rstn) begin
        if (!src_rstn) bin <= '0;
        else if (incr) bin <= bin + 1'b1;
    end

    wire [W-1:0] gray = (bin >> 1) ^ bin;

    logic [W-1:0] gray_sync;
    synchronizer #(.WIDTH(W), .STAGES(2)) u_sync (
        .clk          (dst_clk),
        .async_signal (gray),
        .sync_signal  (gray_sync)
    );

    // Gray to binary: binary[i] = XOR reduction of gray_sync[W-1:i].
    genvar gi;
    generate
        for (gi = 0; gi < W; gi = gi + 1) begin : gen_g2b
            assign count_dst[gi] = ^(gray_sync >> gi);
        end
    endgenerate

endmodule
