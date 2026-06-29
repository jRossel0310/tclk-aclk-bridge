// rtl/aclk_lite/tclk_readout_top.sv
//
// PL readout block for the Fermilab TCLK timing link on the KR260. It wires the
// inherited biphase-mark receiver (TCLK_RCV = serdec4_9MHz + TCLK_DESERIALIZER2)
// to the decoder-agnostic AXI-Lite readout (aclk_readout_axi), so decoded TCLK
// event bytes are timestamped, buffered across the clock-domain crossing, and
// read by the PS over AXI4-Lite (LPD base 0x8000_0000).
//
// Clocking: TCLK is 10 MHz biphase-mark. serdec oversamples it on clk_80m
// (80 MHz, 8x); the deserializer assembles bytes on clk_40m (40 MHz) and emits
// DATA[7:0] + a one-cycle active-low DAVn strobe. The readout's event domain is
// clk_40m (where DAVn / DATA are valid), so its hardware timestamp ticks at
// 40 MHz. On the board clk_80m + clk_40m come from an MMCM / clock wizard.
//
// Adapter (TCLK_RCV -> readout):
//   - aclk_valid = ~DAVn                  (one clk_40m strobe per decoded byte)
//   - aclk_event = { 8'h00, DATA }        (TCLK is an 8-bit event code)
//   - aclk_data  = 0                      (TCLK events carry no 64-bit payload)
//   - flags      = { 14'b0, is_tclk=1, has_data=0 } = 16'h0002
//   - aclk_error = a one-cycle pulse on each new parity error (see below)
//
// The readout's 0xFF null-drop is an ACLK-Lite convention; for TCLK 0xFF can be
// a real code, so this top sets DROP_NULL=0 and every decoded byte is buffered.
//
// Parity error: TCLK_DESERIALIZER2's PERR is a sticky latch (held until
// PERR_CLR). Feeding it straight into the readout's error counter would re-count
// every clock it stays high, so this top edge-detects PERR into a single-cycle
// aclk_error strobe and auto-asserts PERR_CLR the next cycle to re-arm it for the
// next bad frame. serdec emits one spurious PERR while first locking to the
// carrier, so the very first error pulse after bring-up is expected; the PS
// should read ERROR_COUNT as a delta from a post-bring-up baseline.

`timescale 1ns / 1ps

module tclk_readout_top #(
    parameter int ADDR_WIDTH = 6,          // FIFO depth = 2**ADDR_WIDTH
    parameter int AXI_ADDR_W = 8,
    parameter bit USE_EXT_TS = 1'b0        // 0 = internal ts counter (default); 1 = use ts_ext
) (
    // ---- TCLK receive domain ----
    input  logic        clk_80m,           // 80 MHz serdec oversample clock
    input  logic        clk_40m,           // 40 MHz deserializer + readout / timestamp clock
    input  logic        rstn,              // async, active-low (rx side)
    input  logic        pps,               // optional White Rabbit pulse-per-second
    input  logic        tclk,              // raw biphase-mark TCLK line (LVCMOS33 baseband)
    input  logic        mmcm_locked,       // MMCM locked (async) -> AXI 0x30 diagnostic

    // ---- AXI4-Lite slave (PS clock) ----
    input  logic                   s_axi_aclk,
    input  logic                   s_axi_aresetn,
    input  logic [AXI_ADDR_W-1:0]  s_axi_awaddr,
    input  logic                   s_axi_awvalid,
    output logic                   s_axi_awready,
    input  logic [31:0]            s_axi_wdata,
    input  logic [3:0]             s_axi_wstrb,
    input  logic                   s_axi_wvalid,
    output logic                   s_axi_wready,
    output logic [1:0]             s_axi_bresp,
    output logic                   s_axi_bvalid,
    input  logic                   s_axi_bready,
    input  logic [AXI_ADDR_W-1:0]  s_axi_araddr,
    input  logic                   s_axi_arvalid,
    output logic                   s_axi_arready,
    output logic [31:0]            s_axi_rdata,
    output logic [1:0]             s_axi_rresp,
    output logic                   s_axi_rvalid,
    input  logic                   s_axi_rready,

    // ---- external shared timestamp (from global_timebase, A2) ----
    input  logic [63:0] ts_ext,            // shared 64-bit timebase; sampled at each event VALID

    // ---- debug (clk_40m domain) ----
    output logic        dbg_dav,           // ~DAVn: one strobe per decoded byte
    output logic [7:0]  dbg_data,          // decoded event byte
    output logic        dbg_perr,          // raw sticky PERR from the deserializer
    output logic        dbg_sig_err,       // serdec carrier / signal error
    output logic        dbg_hb,            // deep cdc heartbeat[12] -> pin probe
    output logic        dropped_null       // tied 0 (DROP_NULL=0); parity with the ACLK top
);

    // ---- inherited biphase-mark receiver ----
    wire [7:0] data;
    wire       davn;
    wire       perr;
    wire       sig_err;
    logic      perr_clr;

    TCLK_RCV u_rcv (
        .RESETn      (rstn),
        .CLK_40M     (clk_40m),
        .CLK_80M     (clk_80m),
        .TCLK        (tclk),
        .TCLK_RATE   (1'b1),               // 10 MHz mode
        .DATA        (data),
        .DAVn        (davn),
        .SCLK        (),
        .TCLK_CAR    (),
        .TCLK07n     (),
        .PERR        (perr),
        .PERR_CLR    (perr_clr),
        .SIG_ERR     (sig_err),
        .SIG_ERR_CLR (1'b0)
    );

    // ---- parity-error edge detect + auto re-arm (clk_40m domain) ----
    // PERR is sticky; turn its rising edge into a one-cycle error strobe and
    // clear it the next cycle so the next bad frame is also caught. PERR_int_set
    // has priority over PERR_CLR inside the deserializer, so no error is missed.
    logic perr_d;
    always_ff @(posedge clk_40m or negedge rstn) begin
        if (!rstn) begin
            perr_d   <= 1'b0;
            perr_clr <= 1'b0;
        end else begin
            perr_d   <= perr;
            perr_clr <= perr;              // assert PERR_CLR the cycle after PERR is seen high
        end
    end
    wire perr_pulse = perr & ~perr_d;      // one clk_40m cycle per new parity error

    // ---- raw-TCLK activity diagnostic (-> DEBUG register 0x28) ----
    // 2-FF synchronize the raw line into clk_80m (80 MHz oversamples the <=20 MHz
    // biphase edge rate), count every transition, and cross the count to the AXI
    // domain with the same Gray-coded counter the readout uses elsewhere.
    logic tclk_m, tclk_s, tclk_s_d;
    always_ff @(posedge clk_80m or negedge rstn) begin
        if (!rstn) begin
            tclk_m   <= 1'b0;
            tclk_s   <= 1'b0;
            tclk_s_d <= 1'b0;
        end else begin
            tclk_m   <= tclk;
            tclk_s   <= tclk_m;
            tclk_s_d <= tclk_s;
        end
    end
    wire tclk_edge = tclk_s ^ tclk_s_d;          // one clk_80m pulse per transition

    wire [29:0] edge_count;
    cdc_gray_count #(.W(30)) u_cnt_edge (
        .src_clk(clk_80m), .src_rstn(rstn), .incr(tclk_edge),
        .dst_clk(s_axi_aclk), .count_dst(edge_count));

    // Live level + serdec carrier error, synchronized into the AXI domain.
    logic lvl_m, lvl_s, serr_m, serr_s;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            lvl_m  <= 1'b0; lvl_s  <= 1'b0;
            serr_m <= 1'b0; serr_s <= 1'b0;
        end else begin
            lvl_m  <= tclk;    lvl_s  <= lvl_m;
            serr_m <= sig_err; serr_s <= serr_m;
        end
    end

    wire [31:0] tclk_dbg_word = {serr_s, lvl_s, edge_count};

    // ---- adapter: TCLK_RCV -> readout ----
    wire        adapt_valid = ~davn;
    wire [15:0] adapt_event = {8'h00, data};
    wire [15:0] adapt_flags = 16'h0002;    // is_tclk=1 (bit1), has_data=0 (bit0)

    assign dbg_dav     = adapt_valid;
    assign dbg_data    = data;
    assign dbg_perr    = perr;
    assign dbg_sig_err = sig_err;

    // ---- readout + AXI-Lite slave (null-drop disabled for TCLK) ----
    aclk_readout_axi #(
        .ADDR_WIDTH  (ADDR_WIDTH),
        .AXI_ADDR_W  (AXI_ADDR_W),
        .DROP_NULL   (1'b0),
        .USE_EXT_TS  (USE_EXT_TS)
    ) u_axi (
        .rx_clk        (clk_40m),
        .rx_rstn       (rstn),
        .pps           (pps),
        .aclk_valid    (adapt_valid),
        .aclk_event    (adapt_event),
        .aclk_data     (64'd0),
        .flags         (adapt_flags),
        .ts_ext        (ts_ext),
        .aclk_error    (perr_pulse),
        .dropped_null  (dropped_null),
        .dbg_word      (tclk_dbg_word),
        .mmcm_locked   (mmcm_locked),
        .dbg_hb        (dbg_hb),

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
        .s_axi_rready  (s_axi_rready)
    );

endmodule
