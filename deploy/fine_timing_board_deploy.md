# Fine-TDC board deploy + test runbook

End-to-end commands to build the fine-timing bitstream, deploy it to the KR260
in a **new, isolated folder** (leaving the existing `~/aclk_pipeline/` deploy
untouched so you can revert instantly), capture a CSV, bring it back, and
re-check the data. Validation detail (what the numbers should be) lives in
[tclk_fine_timing_bringup.md](tclk_fine_timing_bringup.md); this file is the
logistics.

Placeholders used below (edit to taste):
- Board: `ubuntu@aclk-timestamper.fnal.gov`
- New board folder: `~/finetdc` (the existing `~/aclk_pipeline` is left alone)
- New PC folder for artifacts + returned CSVs: `C:\path\to\board-finetdc`
- Repo root (PC): `C:\path\to\kria-2-hardware`

---

## 1. Build the bitstream (PC, Vivado)

The 4-phase `clk_wiz` is the FIRST synth of this build, so watch timing closure.

```powershell
cd C:\path\to\kria-2-hardware
.\hw.ps1 build
```

Note the printed **MD5** (also in `build-manifest.json`). In the Vivado log, find:

```
TIMING VERIFY (impl_1, post-route): target clk_80m=80 MHz clk_40m=200 MHz (decoupled)
  STATS.WNS = <value> ns      <-- must be >= 0
```

Artifact:
`build\kria\aclk_pipeline\aclk_pipeline.runs\impl_1\uart_echo_bd_wrapper.bit.bin`
(If WNS < 0, stop: the phase clocks did not close timing; do not deploy.)

Stage everything to copy into one folder:

```powershell
mkdir C:\path\to\board-finetdc 2>$null
cd C:\path\to\board-finetdc
copy ..\kria-2-hardware\build\kria\aclk_pipeline\aclk_pipeline.runs\impl_1\uart_echo_bd_wrapper.bit.bin .
copy ..\kria-2-hardware\deploy\aclk_pipeline.dts .
copy ..\kria-2-hardware\deploy\readout_common.py .
copy ..\kria-2-hardware\deploy\tclk_read.py .
copy ..\kria-2-hardware\deploy\tclk_filter.py .
copy ..\kria-2-hardware\deploy\wr_time.py .
copy ..\kria-2-hardware\deploy\aclk_read.py .
copy ..\kria-2-hardware\deploy\redis_publish.py .
copy ..\kria-2-hardware\deploy\redis_sink.py .
copy ..\kria-2-hardware\deploy\stream_archive.py .
copy ..\kria-2-hardware\deploy\stats_log.py .
copy ..\kria-2-hardware\deploy\stats_report.py .
copy ..\kria-2-hardware\deploy\run_pipeline.sh .
copy ..\kria-2-hardware\deploy\requirements-board.txt .
copy ..\kria-2-hardware\deploy\redis-kr260.conf .
copy ..\kria-2-hardware\deploy\tclk_flags_capture.py .   # NEW: fine-FLAGS capture
```

---

## 2. Kerberos ticket (PC, lab network / VPN)

`aclk-timestamper.fnal.gov` uses GSSAPI, so Git Bash `scp` fails; use PuTTY `pscp`
with a live ticket.

```powershell
klist              # look for a non-expired krbtgt/FNAL.GOV
kinit <principal>  # renew if expired (expired ticket = the usual GSSAPI failure)
```

---

## 3. Transfer to the NEW board folder (preserves the old one)

```powershell
# create the isolated folder on the board and copy the whole staged set into it
ssh ubuntu@aclk-timestamper.fnal.gov "mkdir -p ~/finetdc"     # or use PuTTY session
pscp -scp -r "C:\path\to\board-finetdc\*" ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/finetdc/
```

(If Git Bash `ssh` also can't auth, make the folder from a PuTTY terminal, or
`pscp` a dummy file to `~/finetdc/` which creates the path.)

---

## 4. Load the bitstream (board, in the new folder)

```bash
ssh ubuntu@aclk-timestamper.fnal.gov          # PuTTY session
cd ~/finetdc
md5sum uart_echo_bd_wrapper.bit.bin           # MUST equal the PC-side MD5 from step 1
dtc -@ -O dtb -o aclk_pipeline.dtbo aclk_pipeline.dts
sudo xmutil unloadapp
sudo fpgautil -b ~/finetdc/uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
grep . /sys/class/uio/uio*/name               # note tclk/aclk/wr indices (examples: uio4/uio5/uio6)
```

The overlay identity is unchanged (`uart_echo_bd`), so the existing `.dts`
compiles the same `.dtbo`; only the `.bit.bin` differs from the shipped build.
Do NOT use `fpgautil -f Full` (no UIO device, every AXI access bus-errors).

---

## 5. Arm White Rabbit + Phase A capture (decode-regression CSV)

Phase A confirms the new bitstream still decodes TCLK faithfully (the standard
`events-*.csv` carries `id,sec,ns,event,data` -- no fine bits yet).

```bash
cd ~/finetdc
sudo python3 wr_time.py /dev/uio6 status      # pps_alive=1 clk10_alive=1 CELLS_LAST ~= 10000000
sudo python3 wr_time.py /dev/uio6 arm
sudo python3 wr_time.py /dev/uio6 status      # want locked_tclk=1 locked_aclk=1 locked_mon=1
redis-cli ping                                # PONG (start redis with redis-kr260.conf if needed)

# quick decode sanity first (Ctrl-C after ~15 s): EVT climbing, ERR not climbing
sudo python3 -u tclk_read.py /dev/uio4 --wr

# unattended capture -> writes events-tclk-YYYYMMDD.csv in ~/finetdc via stream_archive
sudo ./run_pipeline.sh /dev/uio4 /dev/uio5 /dev/uio6
sudo tmux attach -t kr260                     # watch; Ctrl-b d to detach
# let it run 10-30 min for a decent marker sample, then:
sudo tmux kill-session -t kr260
ls -lh ~/finetdc/events-tclk-*.csv
```

---

## 6. Bring the CSV back + re-check decode faithfulness (PC)

```powershell
pscp -scp "ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/finetdc/events-tclk-*.csv" C:\path\to\board-finetdc\
cd C:\path\to\kria-2-hardware\deploy

# decode faithfulness: should match the prior clean captures (100% defined codes, 0 invalid, 0 $FE)
python tclk_faithfulness.py ..\..\board-finetdc\events-tclk-*.csv

# coarse jitter baseline for comparison with the refined path later
python marker_timing.py ..\..\board-finetdc\events-tclk-*.csv -o ..\..\board-finetdc\marker_coarse.png
```

Success = decode integrity as clean as the old captures (the fine-TDC is
additive, so it must not have perturbed decode). If invalid codes or ERR appear,
stop and debug the build before trusting fine timing.

---

## 7. Phase B: fine-FLAGS capture + calibrate/refine (the new-feature test)

The standard pipeline drops FLAGS, so use the dedicated drain to record the fine
bits. (Stop `run_pipeline.sh` first -- one reader owns the FIFO at a time.)

```bash
# board, ~/finetdc, WR still armed+locked
sudo python3 -u tclk_flags_capture.py /dev/uio4 -o events-tclk-flags.csv --seconds 900
ls -lh events-tclk-flags.csv     # columns: id,sec,ns,event,fine_phase,fine_valid
```

Bring it back and run the fine-timing analysis:

```powershell
pscp -scp "ubuntu@aclk-timestamper.fnal.gov:/home/ubuntu/finetdc/events-tclk-flags.csv" C:\path\to\board-finetdc\
cd C:\path\to\kria-2-hardware\deploy
```

```python
# python (in deploy\), following tclk_fine_timing_bringup.md Steps 3-5:
import numpy as np, csv
from fine_calibrate import calibrate_bins, refine

rows = list(csv.reader(open(r"..\..\board-finetdc\events-tclk-flags.csv")))[1:]
sec  = np.array([int(r[1]) for r in rows]); ns = np.array([int(r[2]) for r in rows])
ev   = np.array([int(r[3]) for r in rows])
fp   = np.array([int(r[4]) for r in rows]); fv = np.array([int(r[5]) for r in rows])

# Step 3 - fine_valid health on the live line (want > ~95% on markers)
m = ev == 0x02                              # the 5 s marker (or 0x8F = 1 s GPS)
print("fine_valid on $02:", fv[m].mean())

# Step 4 - code-density bin calibration (period 5 ns = the 200 MHz coarse tick)
off = calibrate_bins(fp[fv == 1], n_bins=4, period_ns=5.0)
print("bin offsets (ns):", off)

# Step 5 - refined coarse+fine timestamp (marker_timing then compares jitter)
# IMPORTANT: absolute WR ns (~1.78e18) do NOT fit in float64 to ns precision, so
# refine() on absolute stamps silently loses the sub-ns offset. Work RELATIVE to
# the first second (a ~15 min run stays < ~1e12 ns, float64-exact to the ns).
coarse_ns = ((sec - sec[0]).astype(np.int64) * 1_000_000_000 + ns).astype(float)
refined_ns = refine(coarse_ns, fp, fv, off) # + sub-bin where fine_valid
# compare spreads: refined should be tighter than coarse for the marker
d_coarse  = np.diff(coarse_ns[m]);  d_refined = np.diff(refined_ns[m])
print("coarse std (ns):", d_coarse.std(), " refined std (ns):", d_refined.std())
```

Then feed a coarse-only and a refined CSV through `marker_timing.py` and compare
the reported stamp jitter -- success is the refined spread tightening toward
~1.25 ns. See [tclk_fine_timing_bringup.md](tclk_fine_timing_bringup.md) Steps 5
and **5a** (bin-0 continuity) for the exact criteria and the boundary-bin check.

---

## 8. Revert (if anything looks wrong)

Nothing above touches `~/aclk_pipeline/`. To go back to the shipped build:

```bash
sudo xmutil unloadapp
cd ~/aclk_pipeline
sudo fpgautil -b ~/aclk_pipeline/uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
```

The fine-TDC is also a graceful fallback even while loaded: if `fine_valid` is
low on the real line, ignore FLAGS[4:2] and use the 64-bit coarse TS -- that path
is bit-identical to the shipped build (see the bring-up doc's Graceful Fallback).
