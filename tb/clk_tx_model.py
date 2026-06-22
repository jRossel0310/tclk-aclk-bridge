"""Real-framing ACLK-Lite / TCLK transmit model for driving rtl/aclk_lite/clk_rcv
in simulation. Builds on the biphase-mark TCLK model (tb/tclk_tx_model.py): a frame
is one or more bytes, each byte = start(0) + 8 data (MSB first) + even parity, sent
back-to-back with NO gap between bytes; the frame ends when idle (logical 1) cells
follow the last byte's parity. Frame length selects the type: 1 byte = TCLK event,
2 = ACLK event, 12 = full ACLK packet (event[0:1] + data[2:9] + CRC[10] + control[11]).
"""
from tclk_tx_model import event_bits, biphase_samples, drive_samples, SAMPLES_PER_CELL, HALF


def frame_bits(byte_list, bad_idx=None):
    """Concatenate per-byte framings (start + 8 + parity) back-to-back. bad_idx flips
    the parity of that byte index (to exercise the error path)."""
    bits = []
    for i, b in enumerate(byte_list):
        bits += event_bits(b, bad_parity=(bad_idx == i))
    return bits


def stream_frames(frames, warmup_cells=40, gap_cells=12, level=1):
    """Idle warm-up, then each frame followed by an idle gap. Each entry in `frames`
    is a list of byte ints, or a (byte_list, bad_idx) tuple for a bad-parity frame."""
    samples, level = biphase_samples([1] * warmup_cells, level)
    for f in frames:
        byte_list, bad_idx = (f if isinstance(f, tuple) else (f, None))
        s, level = biphase_samples(frame_bits(byte_list, bad_idx), level)
        samples += s
        g, level = biphase_samples([1] * gap_cells, level)
        samples += g
    return samples
