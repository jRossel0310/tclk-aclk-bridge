# TCLK Fine-Timing Board Bring-Up Checklist

Validation procedure for the multiphase fine-TDC feature when the servers return. The fine-timing path is transparent on the real line: if `fine_valid` collapses due to real-line ringing, the coarse 200 MHz timestamp is exactly the shipped build, so nothing is lost.

---

## Step 1: Build and Timing Verification

**Objective:** Confirm the 5-output clk_wiz (80 MHz + 4x 200 MHz phase-shifted) closes timing.

1. **Build the bitstream:**
   ```bash
   cd vivado
   powershell -NoProfile -ExecutionPolicy Bypass -File hw.ps1 build -Tcl build_aclk_pipeline.tcl -Name aclk_pipeline
   ```
   The script calls `vivado` in batch mode to run `vivado/build_aclk_pipeline.tcl`. The Vivado log will be in `~/kria-builds/aclk_pipeline/` (or `$KRIA_BUILD_DIR` if set).

2. **Verify timing closed:** Look for the TIMING VERIFY block in the build log:
   ```
   TIMING VERIFY (impl_1, post-route): target clk_80m=80 MHz clk_40m=200 MHz (decoupled)
     STATS.WNS           = <value> ns
     STATS.TNS           = <value> ns
   ```
   Success: `WNS >= 0 ns` (zero or positive slack).
   
   The build script also emits the worst-setup path and writes a full timing summary to `timing_summary_routed.rpt` in the build directory. The binding domain should be **clk_40m (200 MHz deserializer/readout), NOT clk_80m** (serdec stays at 80 MHz per the DECOUPLED build; see lines 139-151 of `vivado/build_aclk_pipeline.tcl`).

3. **Locate the bitstream:**
   ```
   ls ~/kria-builds/aclk_pipeline/aclk_pipeline.runs/impl_1/uart_echo_bd_wrapper.bit.bin
   ```
   The bitstream is named `uart_echo_bd_wrapper.bit.bin` to preserve the existing overlay identity (so `fpgautil` loads it as the familiar `aclk_pipeline.dtbo` overlay).

---

## Step 2: Flash and Decode Regression Check

**Objective:** Confirm decode is unchanged vs a baseline run (before the fine-TDC was added).

1. **Flash the bitstream on the board:**
   ```bash
   # On the KR260, in /root/aclk_pipeline:
   sudo xmutil unloadapp
   sudo fpgautil -b ~/<path-to>/uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
   ```

2. **Establish baseline:** Before validating fine-timing, confirm the coarse path works exactly as before:
   - Arm White Rabbit (required for timestamping):
     ```bash
     sudo python3 wr_time.py /dev/uio6 arm
     sudo python3 wr_time.py /dev/uio6 status   # want locked_tclk=1 locked_aclk=1 locked_mon=1
     ```
   - Warm up and capture event counters:
     ```bash
     # Let the receiver lock for ~10 seconds
     sudo python3 tclk_read.py /dev/uio4 --wr | head -20
     ```
   - Record the initial `ERROR_COUNT` (print "stats" line every ~1 s):
     ```
     [stats] EVT=<N> NULL=<M> ERR=<baseline_err> FILT=<F> | ...
     ```
     Note `baseline_err`.

3. **Capture a warm-up window:** Let the reader run for ~60 seconds:
   ```bash
   sudo python3 tclk_read.py /dev/uio4 --wr 2>&1 | tee warmup.log
   # Ctrl-C after ~60 s
   ```
   Tail the log for the final stats line:
   ```
   [stats] EVT=<N> NULL=<M> ERR=<warm_err> FILT=<F> | ...
   ```

4. **Verify regression:** Check that the error-count delta is **zero or expected**:
   - Delta = `warm_err - baseline_err`
   - **Success:** Delta ≤ 1 (the one spurious PERR on first lock, documented in `rtl/aclk_lite/tclk_readout_top.sv` lines 40-47, is expected and should not repeat).
   - **Regression:** Delta > 1 or climbing. If the coarse path is broken, the fine-TDC feature is moot; stop here and debug the bitstream.
   - **Sanity:** `EVT` should be climbing (at least 10 events/s on live TCLK). If flat, the receiver is not decoding; check the MMCM lock bit, the TCLK pin wiring, and the serdec signal error bit.

---

## Step 3: Fine-Valid Confirmation on the Live Line

**Objective:** Confirm `fine_valid` (FLAGS bit 4) stays high under real-line conditions.

The FLAGS register layout (from `rtl/aclk_lite/tclk_readout_top.sv` line 247):
- `FLAGS[4]` = `frozen_valid` (fine-TDC sub-bin validity: 1 = in-phase, 0 = lost to ringing)
- `FLAGS[3:2]` = `frozen_phase` (2-bit fine sub-bin: 0, 1, 2, 3 for 90-degree quadrants)
- `FLAGS[1]` = `is_tclk` = 1 (always)
- `FLAGS[0]` = `has_data` = 0 (always, TCLK has no payload)

1. **Read the full event stream with FLAGS:**
   ```bash
   # Modify tclk_read.py temporarily to print FLAGS (or write a one-off script).
   # The event tuple is (ts, event_code, flags, data[63:0]), unpacked from the 160-bit
   # FIFO word: { FLAGS[15:0], TS[63:0], EVENT[15:0], DATA[63:0] }
   # See readout_common.py lines 21-26 for register offsets: STATUS, EVENT, ..., POP.
   ```

   Alternatively, write a quick script to drain the FIFO and log FLAGS:
   ```python
   import readout_common as rc
   io = rc.open_dev("/dev/uio4")
   for _ in range(1000):  # 1000 events
       if not (io.rd(rc.STATUS) & 1):  # not empty
           ev = io.rd(rc.EVENT)  # returns {FLAGS[15:0], EVENT[15:0]}
           event = ev & 0xFFFF
           flags = (ev >> 16) & 0xFFFF
           _ = io.rd(rc.DATA_HI)
           _ = io.rd(rc.DATA_LO)
           _ = io.rd(rc.TS_HI)
           _ = io.rd(rc.TS_LO)
           io.wr(rc.POP, 0)  # pop the event
           fine_valid = (flags >> 4) & 0x1     # FLAGS[4]
           fine_phase = (flags >> 2) & 0x3     # FLAGS[3:2]
           print(f"fine_phase={fine_phase} fine_valid={fine_valid}")
   ```
   (FLAGS packed in upper 16 bits of EVENT register; see `readout_common.py` line 21 and `rtl/aclk_readout/aclk_readout_axi.sv` line 16.)

2. **Analyze the distribution of `fine_valid`:**
   - Capture 1000+ marker events ($02 every 5 s, or $8F every 1 s):
     ```bash
     # One approach: use the capture infrastructure (deploy/capture.md):
     cd aclk_pipeline
     sudo ./run_pipeline.sh /dev/uio4 /dev/uio5 /dev/uio6  # 60 s unattended
     # Then parse the Redis streams or CSV exports.
     ```
   - Count how many have `fine_valid=1` (in-phase) vs `fine_valid=0` (lost):
     - **Success:** >95% of marker events have `fine_valid=1`. Real-line jitter is smaller than a 1.25 ns bin.
     - **Warning:** 50–95% valid. The fine-TDC is working intermittently; real-line ringing is exceeding the ~1.25 ns bin width sporadically. Proceed cautiously; see Step 5 caveats.
     - **Failure:** <50% or majority `fine_valid=0`. The real 3.3 V line's ringing or slow edges defeat the multiphase decode on this board. The coarse path (200 MHz = 5 ns bins) is still valid and the build is deployable, but fine-timing gains are lost. This is a **graceful fallback**: disable the fine-TDC in software by ignoring FLAGS[4] and using only the coarse timestamp.

---

## Step 4: Fine-Bin Calibration (Code Density)

**Objective:** Recover the 1.25 ns sub-bin offsets from the live-line edge distribution.

Prerequisites: Step 3 confirmed `fine_valid` is high on live periodic markers.

1. **Collect raw event CSV:** Export the captured events into `events-tclk-*.csv` format (sec, ns, event, flags):
   ```bash
   # From the capture logs (capture.md, Step 5):
   scp ubuntu@<board>:~/aclk_pipeline/stats-tclk.jsonl .
   # or directly from Redis/database export if available.
   ```

2. **Run calibration:** Use `deploy/fine_calibrate.py`:
   ```python
   import numpy as np
   from fine_calibrate import calibrate_bins
   
   # Extract fine_phase column from the CSV (FLAGS[3:2])
   fine_phase = np.array([...])  # 1000+ samples from marker events
   
   # Recover bin centers (nanoseconds since the coarse-sample time)
   centers = calibrate_bins(fine_phase, n_bins=4, period_ns=5.0)
   # centers = [c0, c1, c2, c3]: time-since-coarse for each 1.25 ns bin
   print("Bin offsets (ns):", centers)
   ```

   The function builds a histogram of fine_phase sample populations, normalizes to fractional widths per bin (representing each bin's true width), and returns the center time of each 1.25 ns bin.

   **Interpretation:**
   - Offsets should be roughly [0.3, 1.55, 2.8, 4.0] ns (even 90-degree quadrants, plus a small skew from board ringing).
   - If one offset is wildly off (e.g., 0.1 or 5.0 ns), that phase is drifting or unstable; recheck the MMCM lock.

3. **Save the offsets:** Store `centers` for Step 5:
   ```python
   np.save("fine_offsets.npy", centers)
   ```

---

## Step 5: Jitter Comparison (Coarse vs. Refined)

**Objective:** Confirm event-to-event timing jitter tightens toward ~1.25 ns resolution with the fine-timing offsets applied.

1. **Prepare two event sets:**
   - **Coarse only:** Use the 200 MHz timestamp from the captured events, ignoring `fine_valid` and `fine_phase`.
   - **Refined:** Apply `fine_calibrate.refine()` to add the sub-bin offset where `fine_valid=1`:
     ```python
     from fine_calibrate import refine
     
     coarse_ns = np.array([...])  # 200 MHz timestamp * 5 ns
     fine_phase = np.array([...])  # FLAGS[3:2]
     fine_valid = np.array([...])  # FLAGS[4]
     offsets = np.load("fine_offsets.npy")  # [c0, c1, c2, c3] from Step 4
     
     refined_ns = refine(coarse_ns, fine_phase, fine_valid, offsets)
     ```

2. **Analyze marker event jitter:** Use `deploy/marker_timing.py` on both sets.
   Marker timing picks a periodic event ($02 every 5 s, or $8F every 1 s) and measures the spread of inter-arrival intervals:
   ```bash
   # Coarse only (baseline):
   python3 marker_timing.py events-tclk-coarse.csv -o marker_timing_coarse.png --event 2
   
   # Refined (with fine-TDC offsets):
   python3 marker_timing.py events-tclk-refined.csv -o marker_timing_refined.png --event 2
   ```

   The output is a 3-panel figure:
   - **(A)** Histogram of per-stamp 2nd differences (short-term jitter): width should narrow.
   - **(B)** Locally-detrended residuals vs time (long-term wander): amplitude should drop.
   - **(C)** Allan deviation of the longest gap-free segment: floor should lower.

   A companion table prints:
   ```
   stamp jitter (ns): <value>  (narrower = success)
   ```

3. **Success criteria:**
   - Coarse jitter: Measure with `deploy/marker_timing.py` on the coarse-only events. Expect order-of-tens-of-ns RMS (200 MHz sampler + cable/board noise).
   - Refined jitter: **<3 ns RMS**, ideally **<2 ns** (approaching the 1.25 ns bin width).
   - Improvement factor: **≥5×** narrower.

4. **Interpretation:**
   - **Strong success** (refined jitter <1.5 ns): Fine-TDC is tracking phase on the real line; the ~1.25 ns bin width is delivering as designed.
   - **Moderate success** (refined jitter 2–3 ns): Working, but board ringing or PLL jitter limits sub-bin precision. Still a 5–10× improvement.
   - **Weak or no improvement**: The fine-valid rate was borderline (Step 3), or the calibration picked up noise instead of real bin structure. Check the fine-valid count again; if <70%, revert to coarse-only timestamps.

---

## Graceful Fallback

If at any point `fine_valid` collapses (Step 3, <50% valid) or jitter does not improve (Step 5), **the coarse 200 MHz path is exactly the shipped build**:

1. Set `USE_EXT_TS=1'b1` in `tclk_readout_top` (line 54 of `rtl/aclk_lite/tclk_readout_top.sv`) if using external White Rabbit timestamping; otherwise the readout uses its internal free-running 200 MHz counter (5 ns resolution).
2. Ignore FLAGS[4:2] in the application layer: use only FLAGS[0:1] and the 64-bit TS field.
3. No rollback bitstream is needed; the feature degrades gracefully to the pre-fine-TDC baseline.

For long-term deployment, if fine-valid < 95% on the real line, disable in firmware (set a mask bit in the fine-TDC to freeze it at 0, or remove the phase-shifted clock connections) to save power and quell any intermittent side effects from an unstable feature.

---

## Summary of Commands and Files

| Step | Tool | File | Key Register / Output |
|------|------|------|----------------------|
| 1 | Vivado TCL | `vivado/build_aclk_pipeline.tcl` | Lines 277–310: timing check (WNS, TNS, worst paths) |
| 2 | Python | `deploy/tclk_read.py` | `readout_common.py` offsets: STATUS (0x00), EVENT_COUNT (0x70), ERROR_COUNT (0x90) |
| 3 | Python | Custom FIFO drain or `deploy/tclk_read.py` + FLAGS parse | FIFO word bits [143:128] = FLAGS[15:0]; FLAGS[4] = fine_valid |
| 4 | Python | `deploy/fine_calibrate.py` → `calibrate_bins()` | Returns 4-element offset array (nanoseconds) |
| 5 | Python | `deploy/fine_calibrate.py` → `refine()` + `deploy/marker_timing.py` | Inter-marker jitter (ns RMS) before/after |

---

## Notes

- The 5-output clk_wiz (80 MHz + 4x 200 MHz phases) is instantiated in `vivado/build_aclk_pipeline.tcl` lines 158–188. The phase outputs (clk_p90, clk_p180, clk_p270) are threaded to `u_pipeline/tclk_readout_top` in the block design and routed to the fine-TDC module (`rtl/aclk_lite/tclk_fine_tdc.sv`).
- The fine-TDC is instantiated in `tclk_readout_top.sv` lines 198–213. It freezes its edge detection on the `ref_edge` signal (the TCLK_DESERIALIZER2's frame-strobe). The readout's event push is delayed 3 clk_40m cycles (ALIGN_DELAY, line 228) to align with the fine-TDC's frozen state settle time, pairing each event with its corresponding frozen triple.
- FLAGS packing is in `tclk_readout_top.sv` line 250: `{11'b0, frozen_valid, frozen_phase, 1'b1, 1'b0}`. The word is latched at push time, so `frozen_valid` and `frozen_phase` are stable when `push_valid` fires (after the 3-cycle alignment delay).
- The calibration and refinement functions (`fine_calibrate.py`) use `+` sign convention (offset added to coarse); Part-2 integration pins the sign against the coarse-latch edge (see the spec comments in that file).
