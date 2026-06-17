// rtl/tclk_readout_bd_top.v
//
// Plain-Verilog block-design wrapper around tclk_readout_top (SystemVerilog). The
// X_INTERFACE attributes let Vivado infer the AXI4-Lite slave (S_AXI) and its
// clock/reset association, so apply_bd_automation can wire the PS LPD master to it.
// pps is tied 0 (no White Rabbit yet); the discrete dbg_* outputs are unused on the
// board (debug is read via the 0x28 DEBUG register over AXI).

`timescale 1ns / 1ps

module tclk_readout_bd_top (
    // receive domain (connected to PS pl_clk1/pl_clk2 + reset in the BD)
    input  wire        clk_80m,
    input  wire        clk_40m,
    input  wire        rstn,
    input  wire        tclk,
    input  wire        mmcm_locked,   // MMCM locked (async) -> AXI 0x30 diagnostic
    output wire        clk40_dbg,     // clk_40m / 1024  -> Pmod pin (scope: is clk_40m alive?)
    output wire        clk100_dbg,    // s_axi_aclk(pl_clk0) / 1024 -> Pmod pin (alive control)
    output wire        cdc_dbg,       // a fresh cdc_gray_count's output bit -> Pmod pin
    output wire        dbg_hb,        // deep readout heartbeat[12] -> Pmod pin

    // AXI4-Lite slave (PS clock); interfaces inferred from the attributes below
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 s_axi_aclk CLK" *)
    (* X_INTERFACE_PARAMETER = "ASSOCIATED_BUSIF S_AXI, ASSOCIATED_RESET s_axi_aresetn" *)
    input  wire        s_axi_aclk,
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 s_axi_aresetn RST" *)
    (* X_INTERFACE_PARAMETER = "POLARITY ACTIVE_LOW" *)
    input  wire        s_axi_aresetn,

    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    input  wire [7:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  wire        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output wire        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  wire [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  wire [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  wire        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output wire        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output wire [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output wire        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  wire        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  wire [7:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  wire        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output wire        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output wire [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output wire [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output wire        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  wire        s_axi_rready
);

    tclk_readout_top #(.ADDR_WIDTH(6), .AXI_ADDR_W(8)) u_tclk (
        .clk_80m       (clk_80m),
        .clk_40m       (clk_40m),
        .rstn          (rstn),
        .pps           (1'b0),
        .tclk          (tclk),
        .mmcm_locked   (mmcm_locked),

        .s_axi_aclk    (s_axi_aclk),
        .s_axi_aresetn (s_axi_aresetn),
        .s_axi_awaddr  (s_axi_awaddr),
        .s_axi_awvalid (s_axi_awvalid),
        .s_axi_awready (s_axi_awready),
        .s_axi_wdata   (s_axi_wdata),
        .s_axi_wstrb   (s_axi_wstrb),
        .s_axi_wvalid  (s_axi_wvalid),
        .s_axi_wready  (s_axi_wready),
        .s_axi_bresp   (s_axi_bresp),
        .s_axi_bvalid  (s_axi_bvalid),
        .s_axi_bready  (s_axi_bready),
        .s_axi_araddr  (s_axi_araddr),
        .s_axi_arvalid (s_axi_arvalid),
        .s_axi_arready (s_axi_arready),
        .s_axi_rdata   (s_axi_rdata),
        .s_axi_rresp   (s_axi_rresp),
        .s_axi_rvalid  (s_axi_rvalid),
        .s_axi_rready  (s_axi_rready),

        .dbg_dav       (),
        .dbg_data      (),
        .dbg_perr      (),
        .dbg_sig_err   (),
        .dbg_hb        (dbg_hb),
        .dropped_null  ()
    );

    // ---- clock-alive scope diagnostics ----
    // The Pmod level translators (~20 Mbit/s) can't pass 40/100 MHz, so divide each
    // clock to ~tens of kHz and drive a pin. A scope then shows -- via a path totally
    // independent of the AXI/CDC/UIO readout -- whether each clock is really toggling.
    // clk100_dbg (from the always-on PS clock) is the control: it MUST toggle, proving
    // the divider->pin->translator->scope path works; clk40_dbg then tests the MMCM.
    reg [9:0] div40  = 10'd0;
    reg [9:0] div100 = 10'd0;
    always @(posedge clk_40m)    div40  <= div40  + 1'b1;
    always @(posedge s_axi_aclk) div100 <= div100 + 1'b1;
    assign clk40_dbg  = div40[9];     // ~39 kHz if clk_40m alive
    assign clk100_dbg = div100[9];    // ~98 kHz (control, from pl_clk0)

    // ---- cdc_gray_count isolation probe ----
    // A fresh, free-running cdc_gray_count (the SAME module the broken-on-hardware
    // counters use), src=clk_40m, dst=s_axi_aclk, read out to a pin. If cdc_dbg is
    // flat while div40 (E10) toggles, the gray-code CDC readback itself is the bug.
    wire [31:0] cdc_test;
    cdc_gray_count #(.W(32)) u_cdc_test (
        .src_clk(clk_40m), .src_rstn(1'b1), .incr(1'b1),
        .dst_clk(s_axi_aclk), .count_dst(cdc_test)
    );
    assign cdc_dbg = cdc_test[12];    // ~4.9 kHz if cdc_gray_count works on silicon

endmodule
