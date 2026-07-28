// rtl/aclk_lite/tclk_fine_tdc.sv
// Multiphase edge time-to-digital: sample the raw line on four 200 MHz clocks at
// 0/90/180/270 deg, synchronize each into the 0-deg domain, and decode the sub-bin
// of a crossing. Off the decode path; a glitch pattern yields fine_valid=0.
`default_nettype none
module tclk_fine_tdc (
    input  wire       rstn,
    input  wire       clk_p0,
    input  wire       clk_p90,
    input  wire       clk_p180,
    input  wire       clk_p270,
    input  wire       line,
    output reg  [1:0]  fine_phase,
    output reg         fine_valid,
    output reg         edge_stb
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
    reg s0_m, s0_s, s90_m, s90_s, s180_m, s180_s, s270_m, s270_s;
    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            {s0_m, s0_s, s90_m, s90_s, s180_m, s180_s, s270_m, s270_s} <= '0;
        end else begin
            s0_m   <= s0_c;    s0_s   <= s0_m;
            s90_m  <= s90_c;   s90_s  <= s90_m;
            s180_m <= s180_c;  s180_s <= s180_m;
            s270_m <= s270_c;  s270_s <= s270_m;
        end
    end

    wire [3:0] samples = {s270_s, s180_s, s90_s, s0_s};  // [3]=latest .. [0]=earliest
    wire [1:0] dphase;
    wire       dvalid;
    tclk_fine_decode u_dec (.samples(samples), .fine_phase(dphase), .fine_valid(dvalid));

    // An edge is "present" this cycle iff the four samples are not all-equal.
    wire present = ~(&samples) & (|samples);

    always @(posedge clk_p0 or negedge rstn) begin
        if (!rstn) begin
            fine_phase <= 2'd0; fine_valid <= 1'b0; edge_stb <= 1'b0;
        end else begin
            edge_stb   <= present;
            fine_valid <= present & dvalid;
            fine_phase <= dphase;
        end
    end
endmodule
`default_nettype wire
