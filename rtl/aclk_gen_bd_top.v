// rtl/aclk_gen_bd_top.v
//
// Plain-Verilog block-design wrapper for the ACLK-Lite signal GENERATOR. Counterpart
// to aclk_readout_bd_top.v, but the generator needs no PS interaction, so this
// wrapper has NO AXI interface: the BD wires only clk_os (the 120 MHz oversample
// clock from the clk_wiz MMCM) and rstn (from the proc_sys_reset). It drives the
// hardcoded timeline's Manchester output onto aclk_out (-> H12) and exposes two
// scope-debug pins: frame_sync_dbg (start-of-trio trigger) and clkos_dbg
// (clk_os / 1024, an MMCM-alive indicator; Pmod level translators cannot pass
// 120 MHz, so divide it down).

`timescale 1ns / 1ps

module aclk_gen_bd_top (
    input  wire clk_os,            // 120 MHz oversample clock (from the BD clk_wiz)
    input  wire rstn,              // active-low reset (from proc_sys_reset)
    output wire aclk_out,          // Manchester ACLK-Lite output -> H12 (idle high)
    output wire frame_sync_dbg,    // start-of-trio pulse -> Pmod (scope trigger)
    output wire clkos_dbg          // clk_os / 1024 -> Pmod (MMCM alive)
);

    wire line;
    wire frame_sync;

    aclk_lite_gen_timeline #(.OVERSAMPLE(12)) u_gen (
        .clk        (clk_os),
        .rstn       (rstn),
        .line       (line),
        .frame_sync (frame_sync)
    );

    assign aclk_out       = line;
    assign frame_sync_dbg = frame_sync;

    // Divide clk_os down so a Pmod level translator can pass it: clkos_dbg ~117 kHz
    // if clk_os is alive.
    reg [9:0] div_os = 10'd0;
    always @(posedge clk_os) div_os <= div_os + 1'b1;
    assign clkos_dbg = div_os[9];

endmodule
