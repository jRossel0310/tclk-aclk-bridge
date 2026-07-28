"""Code-density calibration for the TCLK multiphase fine-timing bins.

An asynchronous line edge lands in each fine-phase bin with probability equal to
that bin's fractional width. So the histogram of fine_phase over many events
recovers the bin widths; the cumulative width gives each bin's center time. Use a
periodic, source-async marker ($02 5 s or $8F 1 Hz) whose sub-tick phase walks
uniformly. Pairs with deploy/marker_timing.py + deploy/tclk_faithfulness.py.
"""
import numpy as np


def calibrate_bins(fine_phase, n_bins=4, period_ns=5.0):
    counts = np.bincount(np.asarray(fine_phase, int), minlength=n_bins)[:n_bins]
    frac = counts / counts.sum()                 # each bin's fractional width
    edges = np.concatenate([[0.0], np.cumsum(frac)]) * period_ns
    centers = 0.5 * (edges[:-1] + edges[1:])      # bin center time, ns
    return centers


def apply(coarse_ns, fine_phase, offsets):
    return np.asarray(coarse_ns, float) + offsets[np.asarray(fine_phase, int)]
