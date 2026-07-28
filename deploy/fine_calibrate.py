"""Code-density calibration for the TCLK multiphase fine-timing bins.

An asynchronous line edge lands in each fine-phase bin with probability equal to
that bin's fractional width. So the histogram of fine_phase over many events
recovers the bin widths; the cumulative width gives each bin's center time. Use a
periodic, source-async marker ($02 5 s or $8F 1 Hz) whose sub-tick phase walks
uniformly. Pairs with deploy/marker_timing.py + deploy/tclk_faithfulness.py.

BOUNDARY BIN (fine_phase==0) -- WHY THERE IS NO PER-TICK "WRAP":
The fine-TDC resolves the boundary quarter (fine_phase==0) one clk_p0 cycle later
than bins 1-3, so a bin-0 edge's packed coarse timestamp is EXACTLY ONE tick
(one period_ns) higher than a physically-adjacent non-boundary edge (RTL sweep
order 1,2,3,0). That +1 tick is NOT spurious jitter to be subtracted: calibrate_bins
indexes offsets by fine_phase and cumulates from bin 0, so it hands the boundary
bin the SMALLEST in-tick offset (~0.6 ns for uniform bins). Combined with refine()'s
`+`, the small offset and the +1 tick cancel and the reconstruction is monotonic in
~1.25 ns steps. Subtracting a period from bin 0 (a "wrap") DOUBLE-COUNTS the tick and
drops bin-0 events ~one period early -- verified to regress ~25 % of events from
~0.6 ns to ~5.4 ns error, worse than coarse-only. So the code applies NO wrap; the
`+` convention below is the implementation. Proof: tb/tclk_fine_tdc/test_tclk_fine_tdc.py
::test_bin0_coarse_reconstruction (real RTL) and test_fine_calibrate.py
::test_refine_boundary_bin_no_wrap_is_correct. This is sim-characterized; end-to-end
board validation of bin-0 continuity is still deferred (see deploy/tclk_fine_timing_bringup.md).
"""
import numpy as np


def calibrate_bins(fine_phase, n_bins=4, period_ns=5.0):
    counts = np.bincount(np.asarray(fine_phase, int), minlength=n_bins)[:n_bins]
    if counts.sum() == 0:
        raise ValueError("calibrate_bins: no fine_phase samples (counts sum to 0)")
    frac = counts / counts.sum()                 # each bin's fractional width
    edges = np.concatenate([[0.0], np.cumsum(frac)]) * period_ns
    centers = 0.5 * (edges[:-1] + edges[1:])      # bin center time, ns
    return centers


def apply(coarse_ns, fine_phase, offsets):
    # SIGN CONVENTION: `+`. offsets are time-since-coarse (from calibrate_bins),
    # added to the packed coarse timestamp. The spec draft wrote `coarse - offset`,
    # but Part-2 characterization against the actual coarse-latch edge showed `+`
    # with calibrate_bins' index-order offsets is the correct implementation (and
    # that no boundary-bin wrap is needed -- see the module docstring). Do not
    # flip the sign or add a wrap without re-running the bin-0 characterization test.
    return np.asarray(coarse_ns, float) + offsets[np.asarray(fine_phase, int)]


def refine(coarse_ns, fine_phase, fine_valid, offsets):
    """Refined timestamp: coarse + calibrated sub-bin offset where fine_valid,
    else coarse alone (graceful fallback). Sign is `+` with calibrate_bins'
    index-order offsets and NO boundary-bin wrap -- the convention verified in
    Part-2 (see the module docstring; the spec's `coarse - offset` was superseded)."""
    coarse_ns = np.asarray(coarse_ns, float)
    out = coarse_ns.copy()
    v = np.asarray(fine_valid, bool)
    fp = np.asarray(fine_phase, int)
    out[v] = coarse_ns[v] + offsets[fp[v]]
    return out
