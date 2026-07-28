// rtl/aclk_lite/tclk_fine_tdc.sv
// Multiphase edge time-to-digital: sample the raw line on four 200 MHz clocks at
// 0/90/180/270 deg, synchronize each into the 0-deg domain, and decode the sub-bin
// of a crossing. The decode window is 5 samples: the four current-period phase
// samples plus the previous period's phase-270 sample delayed one more clk_p0
// cycle (s270_prev). That extra tap makes the window span exactly one period
// across the period boundary, recovering the 4th quarter-period bin that a bare
// 4-sample window would alias away. Off the decode path; a glitch pattern yields
// fine_valid=0.
`default_nettype none
module tclk_fine_tdc (
    input  wire       rstn,
    input  wire       clk_p0,
    input  wire       clk_p90,
    input  wire       clk_p180,
    input  wire       clk_p270,
    input  wire       line,
    input  wire [63:0] coarse_in,
    input  wire        ref_edge,
    output reg  [1:0]  fine_phase,
    output reg         fine_valid,
    output reg         edge_stb,
    output reg  [63:0] frozen_coarse,
    output reg  [1:0]  frozen_phase,
    output reg         frozen_valid
);
    // First-rank capture, one FF per phase in that phase's own clock domain.
    reg s0_c, s90_c, s180_c, s270_c;
    always @(posedge clk_p0)   s0_c   <= line;
    always @(posedge clk_p90)  s90_c  <= line;
    always @(posedge clk_p180) s180_c <= line;
    always @(posedge clk_p270) s270_c <= line;

    // 2-FF synchronize the three off-phase captures into the clk_p0 domain.
    // s0_c is already in the clk_p0 domain (no CDC needed), but it must still get
    // two more clk_p0-domain register stages here so it settles in step with the
    // synchronized s90/s180/s270 samples; otherwise it lands one clk_p0 cycle
    // "ahead" of the others and the {s270,s180,s90,s0} chronological ordering
    // (earliest..latest) breaks.
    reg s0_m, s0_s, s90_m, s90_s, s180_m, s180_s, s270_m, s270_s, s270_prev;
    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            {s0_m, s0_s, s90_m, s90_s, s180_m, s180_s, s270_m, s270_s, s270_prev} <= '0;
        end else begin
            s0_m   <= s0_c;    s0_s   <= s0_m;
            s90_m  <= s90_c;   s90_s  <= s90_m;
            s180_m <= s180_c;  s180_s <= s180_m;
            s270_m <= s270_c;  s270_s <= s270_m;
            s270_prev <= s270_s;   // one more clk_p0 delay: previous period's latest sample
        end
    end

    // [0]=earliest (previous period's s270) .. [4]=latest (this period's s270).
    wire [4:0] samples = {s270_s, s180_s, s90_s, s0_s, s270_prev};
    wire [1:0] dphase;
    wire       dvalid;
    tclk_fine_decode u_dec (.samples(samples), .fine_phase(dphase), .fine_valid(dvalid));

    // An edge is "present" this cycle iff the five-sample window is not
    // all-equal (the period-boundary straddle is now detectable via s270_prev).
    wire present = ~(&samples) & (|samples);

    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            fine_phase <= 2'd0; fine_valid <= 1'b0; edge_stb <= 1'b0;
        end else begin
            edge_stb   <= present;
            fine_valid <= dvalid;  // dvalid (monotone) already implies not-all-equal
            fine_phase <= dphase;
        end
    end

    // "Held last carrier edge": latched every clk_p0 cycle that edge_stb fires,
    // stamped with coarse_in that same cycle. edge_stb/fine_phase/fine_valid are
    // all registered together above (same always block, same clock edge), so
    // this triple is always self-consistent -- including for the boundary-quarter
    // bin (bin 0), which resolves its edge_stb/fine_phase/fine_valid one clk_p0
    // cycle later than bins 1-3 (see module header); edge_coarse/edge_phase/
    // edge_valid simply latch whatever coarse_in reads on that later cycle, so
    // the pairing stays matched. This gives the coarse timestamp no resync
    // jitter beyond the decode pipeline's own fixed latency.
    reg [63:0] edge_coarse;
    reg  [1:0] edge_phase;
    reg        edge_valid;
    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            edge_coarse <= 64'd0; edge_phase <= 2'd0; edge_valid <= 1'b0;
        end else if (edge_stb) begin
            edge_coarse <= coarse_in;
            edge_phase  <= fine_phase;
            edge_valid  <= fine_valid;
        end
    end

    // 2-FF synchronize ref_edge (a clk_40m-domain strobe from the deserializer)
    // into clk_p0, then rising-edge-detect it. clk_p0 and clk_40m are the same
    // 200 MHz clock in this design, but ref_edge originates in different logic
    // (the deserializer's clk_40m domain), so it is treated as a genuine CDC.
    //
    // Total ref_edge -> frozen_* latency, traced through this sync chain and
    // the freeze register below: ref_edge asserted during cycle T -> ref_m
    // valid T+1 -> ref_s valid T+2 -> ref_edge_p0 (=ref_s & ~ref_s_d) pulses
    // ONLY during T+2 -> the freeze register samples that pulse one edge
    // later, so frozen_coarse/frozen_phase/frozen_valid settle to THIS
    // event's values starting cycle T+3. rtl/aclk_lite/tclk_readout_top.sv's
    // ALIGN_DELAY (currently 3) delays its event push by this exact latency
    // to pair each event with its own frozen_* triple; if this sync/freeze
    // pipeline ever gains or loses a stage, ALIGN_DELAY must move with it.
    reg ref_m, ref_s, ref_s_d;
    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            ref_m <= 1'b0; ref_s <= 1'b0; ref_s_d <= 1'b0;
        end else begin
            ref_m   <= ref_edge;
            ref_s   <= ref_m;
            ref_s_d <= ref_s;
        end
    end
    wire ref_edge_p0 = ref_s & ~ref_s_d;

    // Freeze: on the synced ref_edge pulse, capture the held last-carrier-edge
    // triple. Stable until the next ref_edge, regardless of exactly when
    // ref_edge lands within a carrier period.
    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            frozen_coarse <= 64'd0; frozen_phase <= 2'd0; frozen_valid <= 1'b0;
        end else if (ref_edge_p0) begin
            frozen_coarse <= edge_coarse;
            frozen_phase  <= edge_phase;
            frozen_valid  <= edge_valid;
        end
    end
endmodule
`default_nettype wire
