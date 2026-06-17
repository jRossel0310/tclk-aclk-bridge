// rtl/aclk_readout/aclk_readout_axi.sv
//
// AXI4-Lite face of the ACLK readout. It wraps aclk_readout_core (timestamping
// packer + dual-clock FIFO) and presents the buffered events to the PS as a
// small read-mostly register block. The FIFO read side is clocked by the AXI
// clock, so reads and the POP write happen entirely in the s_axi_aclk domain.
//
// Register map. Registers are spaced 16 BYTES apart (not 4): on the KR260 LPD path,
// reads of this hand-written module-reference AXI4-Lite slave only return correct data
// at 16-byte-aligned offsets -- any offset with araddr[3:2]!=0 read back 0 on hardware
// (a packaged AXI IP at the same base read 4-byte-spaced regs fine; root cause unpinned,
// confirmed not the PL fabric/PS-width/Linux-mapping). 16-byte spacing sidesteps it.
// The head event is held stable until POP for a consistent snapshot.
//
//   0x00 STATUS       RO  bit0 = empty, bit1 = overflow (sticky: an event was lost)
//   0x10 EVENT        RO  { FLAGS[15:0], EVENT[15:0] }   of the FIFO head
//   0x20 DATA_HI      RO  DATA[63:32]
//   0x30 DATA_LO      RO  DATA[31:0]
//   0x40 TS_HI        RO  TIMESTAMP[63:32]
//   0x50 TS_LO        RO  TIMESTAMP[31:0]
//   0x60 POP          WO  write (any value) to pop the head and advance
//   0x70 EVENT_COUNT  RO  events enqueued
//   0x80 NULL_COUNT   RO  null / idle packets dropped
//   0x90 ERROR_COUNT  RO  bad-CRC events seen (ACLK_ERROR)
//   0xA0 DEBUG        RO  caller-supplied debug word (TCLK: { sig_err, raw_level,
//                         tclk_transitions[29:0] }; 0 on the ACLK/Manchester path)
//   0xB0 HEARTBEAT    RO  free-running clk_40m counter (cdc_gray_count) - CDC liveness
//   0xC0 LOCK         RO  bit0 = MMCM locked (synchronized)
//   0xD0 CONST        RO  fixed 0xC0FFEE00 (TEMP: wide-value read sanity check)
//
// PS read sequence: poll STATUS; while not empty, read EVENT / DATA_* / TS_*,
// then write POP. The counters cross from the recovered-RX domain through Gray
// code (cdc_gray_count), so they are monotonic and glitch-free when read here.

`timescale 1ns / 1ps

module aclk_readout_axi #(
    parameter int ADDR_WIDTH = 6,          // FIFO depth = 2**ADDR_WIDTH
    parameter int AXI_ADDR_W = 8,          // byte-address width of the register space
    parameter bit DROP_NULL  = 1'b1        // 1: drop 0xFF nulls (ACLK); 0: keep all (TCLK)
) (
    // ---- event input side (recovered-RX domain) ----
    input  logic         rx_clk,
    input  logic         rx_rstn,          // async, active-low
    input  logic         pps,              // optional WR pulse-per-second
    input  logic         aclk_valid,
    input  logic [15:0]  aclk_event,
    input  logic [63:0]  aclk_data,
    input  logic [15:0]  flags,            // per-event metadata (bit0 has_data, bit1 is_tclk)
    input  logic         aclk_error,
    output logic         dropped_null,     // debug passthrough
    input  logic [31:0]  dbg_word,         // RO debug word -> 0x28 (AXI-domain, caller-synced)
    input  logic         mmcm_locked,      // async MMCM locked; synced + read at 0x30
    output logic         dbg_hb,           // rx_heartbeat[12] -> pin: raw deep-cdc value probe

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
    input  logic                   s_axi_rready
);

    // ---------------------------------------------------------------
    // Readout core: timestamping packer + dual-clock FIFO. The FIFO read side
    // lives in the AXI clock domain.
    // ---------------------------------------------------------------
    wire [159:0] head;
    wire         empty;
    wire         overflow;
    logic        pop;

    aclk_readout_core #(.ADDR_WIDTH(ADDR_WIDTH), .DROP_NULL(DROP_NULL)) u_core (
        .rx_clk       (rx_clk),
        .rx_rstn      (rx_rstn),
        .pps          (pps),
        .aclk_valid   (aclk_valid),
        .aclk_event   (aclk_event),
        .aclk_data    (aclk_data),
        .flags        (flags),
        .rd_clk       (s_axi_aclk),
        .rd_rstn      (s_axi_aresetn),
        .rd_en        (pop),
        .rd_data      (head),
        .empty        (empty),
        .overflow     (overflow),
        .dropped_null (dropped_null)
    );

    // Head field views: head = { FLAGS[15:0], TS[63:0], EVENT[15:0], DATA[63:0] }.
    wire [63:0] head_data  = head[63:0];
    wire [15:0] head_evt   = head[79:64];
    wire [63:0] head_ts    = head[143:80];
    wire [15:0] head_flags = head[159:144];

    // ---------------------------------------------------------------
    // Diagnostic counters: incremented in the rx domain, read on the AXI clock.
    // ---------------------------------------------------------------
    // Mirror the core's drop decision so EVENT_COUNT tracks what actually enters
    // the FIFO. With DROP_NULL=0 (TCLK) nothing is a null, so NULL_COUNT stays 0.
    wire core_is_null = DROP_NULL && (aclk_event[7:0] == 8'hFF);
    wire push_evt   = aclk_valid && !core_is_null;
    wire push_null  = aclk_valid &&  core_is_null;

    wire [31:0] event_count, null_count, error_count;

    cdc_gray_count #(.W(32)) u_cnt_evt (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(push_evt),
        .dst_clk(s_axi_aclk), .count_dst(event_count));
    cdc_gray_count #(.W(32)) u_cnt_null (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(push_null),
        .dst_clk(s_axi_aclk), .count_dst(null_count));
    cdc_gray_count #(.W(32)) u_cnt_err (
        .src_clk(rx_clk), .src_rstn(rx_rstn), .incr(aclk_error),
        .dst_clk(s_axi_aclk), .count_dst(error_count));

    // Free-running rx_clk (clk_40m) heartbeat. src_rstn tied high so it counts purely
    // on the clock, NOT gated by rx_rstn -- this isolates "is the rx clock alive" from
    // "is the rx logic held in reset". If reg 0x2C climbs between two AXI reads the MMCM
    // is producing clk_40m; if it is frozen the receive clock is dead (MMCM not locking).
    wire [31:0] rx_heartbeat;
    cdc_gray_count #(.W(32)) u_cnt_hb (
        .src_clk(rx_clk), .src_rstn(1'b1), .incr(1'b1),
        .dst_clk(s_axi_aclk), .count_dst(rx_heartbeat));
    assign dbg_hb = rx_heartbeat[12];   // raw value to a pin: does the deep heartbeat count?

    // MMCM locked, 2-FF synchronized into the AXI domain (read at 0x30). Lets the PS
    // tell "MMCM never locked" (reg=0) apart from "locked but rx clock still dead"
    // (reg=1) -- the AXI domain (s_axi_aclk) is always alive, so this reads even when
    // clk_40m is dead.
    logic [1:0] lock_sync;
    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) lock_sync <= 2'b00;
        else                lock_sync <= {lock_sync[0], mmcm_locked};
    end

    // ---------------------------------------------------------------
    // AXI4-Lite read channel (single outstanding). 16-byte register stride, so the
    // register select is araddr[7:4] (see the register-map note at the top).
    // ---------------------------------------------------------------
    logic        arready_r, rvalid_r;
    logic [31:0] rdata_r;
    wire [AXI_ADDR_W-5:0] rsel = s_axi_araddr[AXI_ADDR_W-1:4];

    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            arready_r <= 1'b1;
            rvalid_r  <= 1'b0;
            rdata_r   <= 32'b0;
        end else if (arready_r && s_axi_arvalid) begin
            arready_r <= 1'b0;
            rvalid_r  <= 1'b1;
            case (rsel)
                'd0:  rdata_r <= {30'b0, overflow, empty};
                'd1:  rdata_r <= {head_flags, head_evt};
                'd2:  rdata_r <= head_data[63:32];
                'd3:  rdata_r <= head_data[31:0];
                'd4:  rdata_r <= head_ts[63:32];
                'd5:  rdata_r <= head_ts[31:0];
                'd7:  rdata_r <= event_count;
                'd8:  rdata_r <= null_count;
                'd9:  rdata_r <= error_count;
                'd10: rdata_r <= dbg_word;
                'd11: rdata_r <= rx_heartbeat;        // 0xB0: free-running clk_40m heartbeat
                'd12: rdata_r <= {31'b0, lock_sync[1]}; // 0xC0: MMCM locked (synced)
                'd13: rdata_r <= 32'hC0FFEE00;        // 0xD0: const - wide-value read sanity (TEMP)
                default: rdata_r <= 32'b0;
            endcase
        end else if (rvalid_r && s_axi_rready) begin
            rvalid_r  <= 1'b0;
            arready_r <= 1'b1;
        end
    end

    assign s_axi_arready = arready_r;
    assign s_axi_rvalid  = rvalid_r;
    assign s_axi_rdata   = rdata_r;
    assign s_axi_rresp   = 2'b00;            // OKAY

    // ---------------------------------------------------------------
    // AXI4-Lite write channel (single outstanding). Only POP has an effect.
    // AW and W are accepted INDEPENDENTLY: the PS interconnect may present the write
    // address a cycle before (or after) the write data, and a slave that only fires
    // when AWVALID and WVALID are both high in the SAME cycle deadlocks -- it completes
    // whichever handshake arrives first, then never sees both high together, so BVALID
    // never asserts and the CPU's store hangs forever (observed on hardware: any POP
    // wedged the LPD bus). Latch each handshake on its own; raise BVALID once both land.
    // ---------------------------------------------------------------
    logic awready_r, wready_r, bvalid_r;
    logic [AXI_ADDR_W-5:0] waddr_q;          // latched write reg-address (16-byte stride)

    always_ff @(posedge s_axi_aclk or negedge s_axi_aresetn) begin
        if (!s_axi_aresetn) begin
            awready_r <= 1'b1;
            wready_r  <= 1'b1;
            bvalid_r  <= 1'b0;
            pop       <= 1'b0;
            waddr_q   <= '0;
        end else begin
            pop <= 1'b0;
            if (s_axi_awvalid && awready_r) begin
                awready_r <= 1'b0;
                waddr_q   <= s_axi_awaddr[AXI_ADDR_W-1:4];
            end
            if (s_axi_wvalid && wready_r)
                wready_r  <= 1'b0;
            if (!awready_r && !wready_r && !bvalid_r) begin
                bvalid_r <= 1'b1;
                if (waddr_q == 'd6 && !empty)
                    pop <= 1'b1;            // POP @ 0x60: advance the FIFO head
            end
            if (bvalid_r && s_axi_bready) begin
                bvalid_r  <= 1'b0;
                awready_r <= 1'b1;
                wready_r  <= 1'b1;
            end
        end
    end

    assign s_axi_awready = awready_r;
    assign s_axi_wready  = wready_r;
    assign s_axi_bvalid  = bvalid_r;
    assign s_axi_bresp   = 2'b00;            // OKAY

endmodule
