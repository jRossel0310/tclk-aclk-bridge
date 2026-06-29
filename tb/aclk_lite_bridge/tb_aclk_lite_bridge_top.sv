`timescale 1ns / 1ps
// tb/aclk_lite_bridge/tb_aclk_lite_bridge_top.sv
//
// Testbench top: wires aclk_lite_bridge -> aclk_lite_encoder (SAMPLES_PER_CELL=8)
// -> clk_rcv decoder. The bridge rx side is driven directly from the testbench;
// the enc side and the clk_rcv decoder share enc_clk (80 MHz). clk_rcv also
// needs a 40 MHz clock for clk_byte_framer.
//
// Exposed to Python:
//   Stimulus:  rx_clk, rx_rstn, enc_clk, enc_rstn, clk_40m
//              aclk_valid, aclk_event[15:0], aclk_data[63:0]
//   Observe:   enc_start, enc_busy, dropped_count[15:0]
//              event_valid, event_id[15:0], data_valid, data[63:0]
//              parity_error, is_tclk, sig_err

module tb_aclk_lite_bridge_top (
    // rx-domain inputs (driven by Python test)
    input  wire        rx_clk,
    input  wire        rx_rstn,
    input  wire        aclk_valid,
    input  wire [15:0] aclk_event,
    input  wire [63:0] aclk_data,

    // encoder / decoder clocks
    input  wire        enc_clk,      // 80 MHz
    input  wire        enc_rstn,
    input  wire        clk_40m,      // 40 MHz for clk_byte_framer

    // observability
    output wire        enc_start,
    output wire        enc_busy,
    output wire [15:0] dropped_count,

    output wire        event_valid,
    output wire [15:0] event_id,
    output wire        data_valid,
    output wire [63:0] data,
    output wire        parity_error,
    output wire        is_tclk,
    output wire        sig_err,
    output wire        enc_line    // encoder Manchester output (for waveform plot)
);

    // ---- bridge outputs to encoder ----
    wire [15:0] w_enc_event_id;
    wire [63:0] w_enc_data;
    wire [1:0]  w_enc_frame_type;
    wire        w_enc_start;
    wire        w_enc_busy;

    aclk_lite_bridge u_bridge (
        .rx_clk       (rx_clk),
        .rx_rstn      (rx_rstn),
        .aclk_valid   (aclk_valid),
        .aclk_event   (aclk_event),
        .aclk_data    (aclk_data),
        .enc_clk      (enc_clk),
        .enc_rstn     (enc_rstn),
        .enc_event_id (w_enc_event_id),
        .enc_data     (w_enc_data),
        .enc_frame_type(w_enc_frame_type),
        .enc_start    (w_enc_start),
        .enc_busy     (w_enc_busy),
        .dropped_count(dropped_count)
    );

    // expose enc_start for the test to monitor
    assign enc_start = w_enc_start;

    // ---- encoder ----
    wire w_enc_line;

    aclk_lite_encoder #(.SAMPLES_PER_CELL(8)) u_enc (
        .clk        (enc_clk),
        .rstn       (enc_rstn),
        .start      (w_enc_start),
        .event_id   (w_enc_event_id),
        .data       (w_enc_data),
        .frame_type (w_enc_frame_type),
        .line       (w_enc_line),
        .busy       (w_enc_busy)
    );

    assign enc_busy = w_enc_busy;
    assign enc_line = w_enc_line;

    // ---- clk_rcv decoder ----
    clk_rcv u_rcv (
        .RESETn      (enc_rstn),
        .CLK_80M     (enc_clk),
        .CLK_40M     (clk_40m),
        .clkline     (w_enc_line),
        .event_valid (event_valid),
        .event_id    (event_id),
        .data_valid  (data_valid),
        .data        (data),
        .parity_error(parity_error),
        .is_tclk     (is_tclk),
        .sig_err     (sig_err)
    );

endmodule
