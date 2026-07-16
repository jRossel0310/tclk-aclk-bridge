#!/usr/bin/env python3
"""Folded supercycle raster + distribution shape for one TCLK event code (PC side).

Reads the CSVs written by stream_archive.py, anchors every event to the
preceding $00 (supercycle reset), folds all supercycles onto one time axis,
and renders: a marginal histogram of the target code's offsets (the shape) on
top of a raster (one row per supercycle, reference-comb events as faint dots,
target events as colored dots). Cycles whose length deviates from the median
by more than --tol are rejected (a missed anchor would fold two cycles).

    python supercycle_plot.py events-tclk-*.csv --target 1E --ref 0C,BA
    python supercycle_plot.py tail.csv --target 1F --theme poster -o bes.png
"""
import argparse
import csv
import sys

import numpy as np


def load_events(paths):
    """CSV file(s) -> (t seconds float64, event int), deduped by stream id,
    stably time-sorted."""
    seen = set()
    t, ev = [], []
    for p in paths:
        with open(p, newline="") as f:
            for row in csv.DictReader(f):
                eid = row["id"]
                if eid in seen:
                    continue
                seen.add(eid)
                t.append(int(row["sec"]) + int(row["ns"]) * 1e-9)
                ev.append(int(row["event"]))
    t = np.asarray(t, dtype=np.float64)
    ev = np.asarray(ev, dtype=np.int64)
    order = np.argsort(t, kind="stable")
    return t[order], ev[order]


def cycles_from_anchors(anchor_t, tol=0.01):
    """Consecutive-anchor windows filtered to |len - median| <= tol*median.
    Returns (starts, ends, stats). Raises ValueError with a clear message when
    fewer than 2 anchors exist."""
    if len(anchor_t) < 2:
        raise ValueError("need at least 2 anchor events to form a cycle "
                         "(got %d)" % len(anchor_t))
    lens = np.diff(anchor_t)
    med = float(np.median(lens))
    keep = np.abs(lens - med) <= tol * med
    stats = {"median_len": med, "n_cycles": int(len(lens)),
             "n_kept": int(keep.sum()), "n_rejected": int((~keep).sum())}
    return anchor_t[:-1][keep], anchor_t[1:][keep], stats


def assign_offsets(t, starts, ends):
    """Per event: (mask in-a-kept-cycle, dense row index, offset seconds).
    row/off are full-length arrays, meaningful only where mask is True."""
    idx = np.searchsorted(starts, t, side="right") - 1
    idx_c = np.clip(idx, 0, len(starts) - 1)
    mask = (idx >= 0) & (t < ends[idx_c])
    off = t - starts[idx_c]
    return mask, idx_c, off
