// rtl/uart_echo_top.sv
//
// UART loopback ("echo") top-level: every byte received on `serial_in` is
// buffered and re-transmitted on `serial_out`. This is the first synthesizable
// design for the Kria KR260 bring-up and is built purely from the existing
// library primitives:
//
//   serial_in --> synchronizer --> uart_receiver --> fifo --> uart_transmitter --> serial_out
//
// `serial_in` is a raw, asynchronous line (e.g. from an FTDI USB-UART adapter on
// a PMOD pin), so it is first passed through a 2-stage synchronizer — the
// uart_receiver explicitly requires its input to already be synchronous to clk.
//
// On the Kria the clock and reset come from the Zynq UltraScale+ PS via the
// block design: `clk` <- pl_clk0 (100 MHz by default, matching CLOCK_FREQ) and
// `reset` <- an active-high reset derived from pl_resetn by a proc_sys_reset IP.

`timescale 1ns / 1ps

module uart_echo_top #(
    parameter int CLOCK_FREQ = 100_000_000,  // must match the PL clock (pl_clk0)
    parameter int BAUD_RATE  = 115_200,
    parameter int FIFO_DEPTH = 32
) (
    input  logic clk,
    input  logic reset,        // synchronous, active-high
    input  logic serial_in,    // raw async RX line, idles high
    output logic serial_out    // TX line, idles high
);

    //--| Synchronize the asynchronous RX line into the clk domain |------------
    logic serial_in_sync;
    synchronizer #(
        .WIDTH  (1),
        .STAGES (2)
    ) u_sync (
        .clk          (clk),
        .async_signal (serial_in),
        .sync_signal  (serial_in_sync)
    );

    //--| Receiver |------------------------------------------------------------
    logic [7:0] rx_data;
    logic       rx_valid;
    logic       rx_ready;

    uart_receiver #(
        .CLOCK_FREQ (CLOCK_FREQ),
        .BAUD_RATE  (BAUD_RATE)
    ) u_rx (
        .clk            (clk),
        .reset          (reset),
        .data_out       (rx_data),
        .data_out_valid (rx_valid),
        .data_out_ready (rx_ready),
        .serial_in      (serial_in_sync)
    );

    //--| FIFO between RX and TX |----------------------------------------------
    logic       fifo_full;
    logic       fifo_empty;
    logic [7:0] fifo_dout;
    logic       fifo_wr_en;
    logic       fifo_rd_en;

    // Push a received byte whenever the receiver has one and the FIFO has room;
    // accept it from the receiver on that same cycle.
    assign fifo_wr_en = rx_valid & ~fifo_full;
    assign rx_ready   = ~fifo_full;

    fifo #(
        .WIDTH (8),
        .DEPTH (FIFO_DEPTH)
    ) u_fifo (
        .clk   (clk),
        .rst   (reset),
        .wr_en (fifo_wr_en),
        .din   (rx_data),
        .full  (fifo_full),
        .rd_en (fifo_rd_en),
        .dout  (fifo_dout),
        .empty (fifo_empty)
    );

    //--| Transmitter |---------------------------------------------------------
    logic [7:0] tx_data;
    logic       tx_valid;
    logic       tx_ready;

    // FWFT FIFO: the head byte is already on `fifo_dout`. Offer it to the TX and
    // pop it on the cycle the TX accepts it (valid && ready both high).
    assign tx_data    = fifo_dout;
    assign tx_valid   = ~fifo_empty;
    assign fifo_rd_en = tx_valid & tx_ready;

    uart_transmitter #(
        .CLOCK_FREQ (CLOCK_FREQ),
        .BAUD_RATE  (BAUD_RATE)
    ) u_tx (
        .clk           (clk),
        .reset         (reset),
        .data_in       (tx_data),
        .data_in_valid (tx_valid),
        .data_in_ready (tx_ready),
        .serial_out    (serial_out)
    );

endmodule
