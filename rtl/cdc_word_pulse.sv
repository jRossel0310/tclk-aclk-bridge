// rtl/cdc_word_pulse.sv
//
// Single-outstanding word-plus-pulse clock-domain crossing (toggle handshake).
// On src_valid the word is captured and a request toggle flips on the SAME src
// edge; the destination 2-FF-syncs the toggle and, on any change, emits a
// 1-cycle dst_valid with the word. Because data_q is stable from the same edge
// the toggle flips and the synced toggle arrives >= 2 dst_clk later, sampling
// data_q in the destination domain is skew-safe by protocol.
//
// Warmup: after a destination-domain reset the current toggle level is adopted
// WITHOUT firing, so a reset while the toggle sits at 1 cannot replay a stale
// transfer (e.g. re-arming the WR timebase with an old seconds value after a
// GT relock).
//
// Contract: src_valid pulses must be spaced >= 3 dst_clk periods (plus 2
// src_clk) apart or a transfer is silently lost. Fine for quasi-static config
// like the WR seconds arm, which software writes at human timescales.

`timescale 1ns / 1ps

module cdc_word_pulse #(
    parameter int W = 32
) (
    input  logic         src_clk,
    input  logic         src_rstn,     // async, active-low
    input  logic         src_valid,    // 1-cycle strobe
    input  logic [W-1:0] src_data,
    input  logic         dst_clk,
    input  logic         dst_rstn,     // async, active-low
    output logic         dst_valid,    // 1-cycle strobe
    output logic [W-1:0] dst_data
);

    // ---- source side: capture the word and flip the request toggle ----
    logic         req_tgl;
    logic [W-1:0] data_q;
    always_ff @(posedge src_clk or negedge src_rstn) begin
        if (!src_rstn) begin
            req_tgl <= 1'b0;
            data_q  <= '0;
        end else if (src_valid) begin
            req_tgl <= ~req_tgl;
            data_q  <= src_data;
        end
    end

    // ---- destination side: sync the toggle, fire on change, sample the word ----
    wire tgl_sync;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync (
        .clk          (dst_clk),
        .async_signal (req_tgl),
        .sync_signal  (tgl_sync)
    );

    logic [1:0] warmup;
    logic       tgl_d;
    always_ff @(posedge dst_clk or negedge dst_rstn) begin
        if (!dst_rstn) begin
            warmup    <= 2'd0;
            tgl_d     <= 1'b0;
            dst_valid <= 1'b0;
            dst_data  <= '0;
        end else if (warmup != 2'd3) begin
            // adopt the current toggle level without firing
            warmup    <= warmup + 2'd1;
            tgl_d     <= tgl_sync;
            dst_valid <= 1'b0;
        end else begin
            tgl_d     <= tgl_sync;
            dst_valid <= (tgl_sync != tgl_d);
            if (tgl_sync != tgl_d) dst_data <= data_q;
        end
    end

endmodule
