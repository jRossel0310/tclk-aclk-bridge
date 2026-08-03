// rtl/wr_timebase.sv
//
// White-Rabbit-disciplined {sec[31:0], ns[31:0]} timebase, generated INSIDE the
// consumer's clock domain. Instantiate one per event domain: every instance
// watches the same two physical pins, so all copies carry the same timeline and
// no 64-bit value ever crosses a clock domain.
//
// ns construction: each detected wr_clk10 rising edge adds 100 ns (one WR
// cell); between edges a fixed-point interpolator adds the local clock period
// (CLK_PERIOD_DS, 0.1 ns units, exact for 25.0 / 16.0 / 10.0 / 6.4 ns), and is
// cleared at every edge so interpolation error never accumulates. A PPS rising
// edge zeroes ns, latches the interval cell count, and loads or increments sec.
//
// STRICT validity: ts reads 64'd0 unless locked. Locked requires a seconds
// value armed through the cfg interface AND loaded at a PPS edge, AND both
// watchdogs alive (a wr_clk10 edge within CLK10_TIMEOUT cycles, a PPS within
// PPS_TIMEOUT cycles). Any violation, or a disarm request, unlocks; re-locking
// requires a fresh arm. ts == 0 therefore means "not WR-synced when stamped".
//
// The cfg_* interface lives in its own clock domain (the AXI clock) and crosses
// in through one cdc_word_pulse, so the AXI slave drives many instances with
// the same wires. cfg_valid strobes with cfg_disarm=0 arm cfg_sec (the Unix UTC
// label of the NEXT PPS); with cfg_disarm=1 they force an unlock.
//
// GLITCH REJECTION: every accepted PPS edge adds a whole second, so one spurious
// edge on the pin puts every later timestamp 1 s into the future while `locked`,
// `pps_alive` and `cells_last` all stay perfectly healthy. That is not
// hypothetical: a 2026-08-03 capture ran 4 s fast for 5 hours with no indication
// anywhere. An edge is therefore accepted only once PPS_MIN_CELLS wr_clk10 cells
// have elapsed since the previous accepted one, and rejections are counted on
// pps_rejected so the condition is observable rather than silent.
//
// The filter is deliberately one-sided: it rejects EARLY edges only. An edge that
// arrives LATE (a genuinely missed pulse) still passes, so the filter can never
// worsen a dropped-pulse case, and the seconds counter never stalls waiting for
// an edge it decided to ignore. The first edge after reset is accepted
// unconditionally, since there is no previous interval to measure against.

`timescale 1ns / 1ps

module wr_timebase #(
    parameter int unsigned CLK_PERIOD_DS = 250,        // local clock period, 0.1 ns units
    parameter int unsigned CLK10_TIMEOUT = 16,         // cycles w/o a 10 MHz edge -> unlock
    parameter int unsigned PPS_TIMEOUT   = 44_000_000, // cycles w/o a PPS edge -> unlock
    // Minimum wr_clk10 cells between accepted PPS edges. A real second is
    // 10,000,000 cells, so 9,000,000 (0.9 s) accepts every genuine edge with wide
    // margin while rejecting any glitch closer than 0.9 s to the last one.
    parameter int unsigned PPS_MIN_CELLS = 9_000_000
) (
    input  logic        clk,
    input  logic        rstn,             // async, active-low
    input  logic        wr_clk10,         // async WR 10 MHz reference (Pmod pin)
    input  logic        wr_pps,           // async WR pulse-per-second (Pmod pin)
    // ---- cfg domain (AXI clock): arm / disarm, CDC'd in internally ----
    input  logic        cfg_clk,
    input  logic        cfg_rstn,
    input  logic        cfg_valid,        // 1-cycle strobe
    input  logic        cfg_disarm,       // qualifies cfg_valid: 1 = disarm
    input  logic [31:0] cfg_sec,          // Unix UTC label of the NEXT PPS
    // ---- outputs (clk domain) ----
    output logic [63:0] ts,               // {sec, ns}; forced to 0 unless locked
    output logic        locked,
    output logic        arm_pending,
    output logic        pps_alive,
    output logic        clk10_alive,
    output logic        pps_edge,         // 1-cycle strobe per ACCEPTED PPS edge
    output logic [31:0] cells_last,       // wr_clk10 cells in the previous PPS interval
    output logic [31:0] pps_rejected      // PPS edges discarded as too early (glitches)
);

    localparam int unsigned PERIOD_NS_INT = CLK_PERIOD_DS / 10;
    // whole-ns and 0.1 ns parts of the local period; FRAC is 0..9 so 4 bits hold it
    localparam logic [3:0] PERIOD_FRAC = CLK_PERIOD_DS % 10;

    // ---- 2-FF sync + rising-edge detect for the WR pins ----
    wire clk10_s, pps_s;
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_clk10 (
        .clk(clk), .async_signal(wr_clk10), .sync_signal(clk10_s));
    synchronizer #(.WIDTH(1), .STAGES(2)) u_sync_pps (
        .clk(clk), .async_signal(wr_pps), .sync_signal(pps_s));

    logic clk10_d, pps_d;
    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            clk10_d <= 1'b0;
            pps_d   <= 1'b0;
        end else begin
            clk10_d <= clk10_s;
            pps_d   <= pps_s;
        end
    end
    wire clk10_re = clk10_s & ~clk10_d;
    wire pps_re   = pps_s   & ~pps_d;

    // ---- arm / disarm from the cfg domain (one toggle-handshake CDC) ----
    wire        req_v;
    wire [32:0] req_w;
    cdc_word_pulse #(.W(33)) u_cfg_cdc (
        .src_clk(cfg_clk), .src_rstn(cfg_rstn),
        .src_valid(cfg_valid), .src_data({cfg_disarm, cfg_sec}),
        .dst_clk(clk), .dst_rstn(rstn),
        .dst_valid(req_v), .dst_data(req_w));
    wire        req_is_disarm = req_w[32];
    wire [31:0] req_sec       = req_w[31:0];
    // A disarm arriving this cycle must win over everything, including a
    // coincident PPS trying to consume a stale pending arm (see PPS branch).
    wire        disarm_now    = req_v && req_is_disarm;

    // ---- watchdogs: saturating, reset to expired so alive starts low ----
    logic [31:0] clk10_wd, pps_wd;
    assign clk10_alive = (clk10_wd < CLK10_TIMEOUT);
    assign pps_alive   = (pps_wd   < PPS_TIMEOUT);

    // The WR PPS is phase-aligned to a wr_clk10 edge, and two independent 2-FF
    // syncs can resolve that shared physical edge one cycle apart. Suppress
    // wr_clk10 edges for 2 cycles after each PPS so the boundary edge cannot
    // double-count as "cell 1" right after ns was zeroed (2 cycles is well
    // under one 100 ns cell in every target domain: 50 ns at 40 MHz).
    // (A PPS-coincident clk10 edge is already excluded by the if (pps_re)
    // priority in the always_ff, so no !pps_re term is needed here.)
    logic [1:0] pps_shadow;
    wire clk10_cell = clk10_re && (pps_shadow == 2'd0);

    // ---- the timebase proper ----
    logic        armed;
    logic [31:0] arm_sec_q;
    logic [31:0] sec;
    logic [31:0] ns_base;      // ns at the last accepted wr_clk10 edge (multiple of 100)
    logic [31:0] interp;       // whole ns accumulated since that edge
    logic [3:0]  interp_frac;  // 0.1 ns remainder of the accumulation
    logic [31:0] cells;
    logic        lk;
    logic        pps_seen;     // an edge has been accepted, so `cells` is meaningful

    // ---- PPS glitch rejection ----
    // `cells` counts wr_clk10 cells since the last ACCEPTED edge, so it already IS
    // the interval measurement this needs; the filter costs one comparator. Before
    // the first accepted edge there is nothing to compare against, so accept.
    wire pps_valid  = pps_re && (!pps_seen || (cells >= PPS_MIN_CELLS));
    wire pps_glitch = pps_re && !pps_valid;
    assign pps_edge = pps_valid;

    wire [4:0] frac_next  = {1'b0, interp_frac} + {1'b0, PERIOD_FRAC};
    wire       frac_carry = (frac_next >= 5'd10);
    wire [4:0] frac_adj   = frac_next - 5'd10;

    always_ff @(posedge clk or negedge rstn) begin
        if (!rstn) begin
            armed       <= 1'b0;
            arm_sec_q   <= '0;
            sec         <= '0;
            ns_base     <= '0;
            interp      <= '0;
            interp_frac <= '0;
            cells        <= '0;
            cells_last   <= '0;
            lk           <= 1'b0;
            pps_shadow   <= 2'd0;
            pps_seen     <= 1'b0;
            pps_rejected <= '0;
            clk10_wd    <= CLK10_TIMEOUT;   // start expired: not alive until edges arrive
            pps_wd      <= PPS_TIMEOUT;
        end else begin
            // Arm / disarm requests. If an arm lands on the same cycle as a PPS
            // edge consuming a PREVIOUS arm, the PPS branch below wins for
            // `armed` and the new arm is lost; software arms mid-second (see
            // deploy/wr_time.py), so that coincidence cannot happen in practice.
            if (req_v) begin
                if (req_is_disarm) begin
                    armed <= 1'b0;
                    lk    <= 1'b0;
                end else begin
                    armed     <= 1'b1;
                    arm_sec_q <= req_sec;
                end
            end

            // Watchdogs: cleared by their edge, saturate at the timeout.
            if (clk10_re)                      clk10_wd <= '0;
            else if (clk10_wd < CLK10_TIMEOUT) clk10_wd <= clk10_wd + 1'b1;
            // Only an ACCEPTED edge counts as liveness. If a glitch could feed the
            // watchdog, a dead PPS line that merely picks up noise would report
            // healthy forever, which is the opposite of what STRICT means here.
            if (pps_valid)                     pps_wd   <= '0;
            else if (pps_wd < PPS_TIMEOUT)     pps_wd   <= pps_wd + 1'b1;

            // Count discards so the condition is visible in the register map
            // instead of being silently absorbed.
            if (pps_glitch) pps_rejected <= pps_rejected + 32'd1;

            // STRICT: a dead reference unlocks; re-locking needs a fresh arm.
            if (!clk10_alive || !pps_alive) lk <= 1'b0;

            if (pps_shadow != 2'd0) pps_shadow <= pps_shadow - 2'd1;

            // A boundary edge that resolves one cycle after the PPS (sync
            // skew) is suppressed as a cell of the new interval; credit it
            // to the interval it closes so cells_last is exact in all three
            // skew alignments (clk10-first, same-cycle, pps-first).
            if (clk10_re && !pps_valid && (pps_shadow != 2'd0))
                cells_last <= cells_last + 32'd1;

            if (pps_valid) begin
                // PPS boundary: zero ns, latch the interval cell count, seconds.
                ns_base     <= '0;
                interp      <= '0;
                interp_frac <= '0;
                // The WR PPS is phase-aligned to a wr_clk10 edge; the two
                // independent 2-FF syncs can resolve that shared physical
                // transition with clk10_re one cycle EARLY, on the SAME
                // cycle, or one cycle LATE relative to pps_re. (In this
                // zero-delay sim the same-cycle case is what deterministically
                // happens; on hardware all three occur.) A same-cycle edge
                // belongs to the interval that just ended, so fold it in
                // here; the late (pps-first) case is credited by the shadow-
                // window statement above; the early (clk10-first) case is
                // already inside `cells`.
                cells_last  <= cells + (clk10_re ? 32'd1 : 32'd0);
                cells       <= '0;
                pps_shadow  <= 2'd2;
                pps_seen    <= 1'b1;
                if (armed) begin
                    sec   <= arm_sec_q;
                    armed <= 1'b0;
                    // Never lock onto a dead 10 MHz, and never let a stale
                    // pending arm override a disarm landing this same cycle.
                    if (clk10_alive && !disarm_now) lk <= 1'b1;
                end else if (lk) begin
                    sec <= sec + 1'b1;
                end
            end else if (clk10_cell) begin
                // WR cell edge: snap ns to cells*100, restart interpolation.
                ns_base     <= ns_base + 32'd100;
                interp      <= '0;
                interp_frac <= '0;
                cells       <= cells + 1'b1;
            end else begin
                // Free-run interpolation between edges, exact in 0.1 ns units.
                interp      <= interp + PERIOD_NS_INT + (frac_carry ? 32'd1 : 32'd0);
                interp_frac <= frac_carry ? frac_adj[3:0] : frac_next[3:0];
            end
        end
    end

    // ns saturates just below 1 s so a late PPS cannot produce a malformed
    // field in the window before the PPS watchdog unlocks.
    wire [31:0] ns_raw = ns_base + interp;
    wire [31:0] ns_sat = (ns_raw > 32'd999_999_999) ? 32'd999_999_999 : ns_raw;

    assign ts          = lk ? {sec, ns_sat} : 64'd0;
    assign locked      = lk;
    assign arm_pending = armed;

endmodule
