// rtl/uart_echo_bd_top.v
//
// Plain-Verilog wrapper around uart_echo_top, used ONLY so the design can be
// dropped into a Vivado block design as a "module reference". Vivado's block
// design module-reference flow rejects a SystemVerilog file as the *top* file of
// the reference, so this thin Verilog top wraps the SystemVerilog implementation
// (whose own submodules stay SystemVerilog — only the top file must be Verilog).
//
// Parameters are fixed here to match the PL clock (pl_clk0 = 100 MHz) set in the
// block design; edit both together if you change the clock.

`timescale 1ns / 1ps

module uart_echo_bd_top (
    input  wire clk,
    input  wire reset,
    input  wire serial_in,
    output wire serial_out
);

    uart_echo_top #(
        .CLOCK_FREQ (100000000),
        .BAUD_RATE  (115200),
        .FIFO_DEPTH (32)
    ) u_impl (
        .clk        (clk),
        .reset      (reset),
        .serial_in  (serial_in),
        .serial_out (serial_out)
    );

endmodule
