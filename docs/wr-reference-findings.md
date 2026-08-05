# White Rabbit reference investigation: findings

Investigation of 2026-08-04, answering [wr-reference-investigation.md](wr-reference-investigation.md).
All analysis was done from the local copy of the weekend capture
(`weekend-20260731/flags-*.csv`, `$8F` markers); the board and the
grandmaster were unreachable from this machine (no lab network access), so the
grandmaster-side confirmation steps remain open and are listed at the end.

## Verdict, in one paragraph

The grandmaster rebooted (or was power-cycled) at 2026-08-03 01:36:36 UTC. That one
event accounts for all three open puzzles: the frequency change, the 89-day TAI
offset, and the "exactly 4.000000 s fast" era, which turns out to be the same event
rather than a finding of its own. The reboot is caught directly in the weekend
capture, as a burst of 4 spurious PPS edges between 01:36:36 and 01:36:43 UTC, three
minutes before the capture ended. Both eras' frequency measurements are correct: the
grandmaster was free-running before the reboot at -3.48 ppm and is free-running after
it at +0.37 ppm, and rebooting an undisciplined master resets its oscillator tune
state, so the output frequency steps. It was not GPS-disciplined in either era, and
was not at any point during this period.

## The event, caught in the data

`$8F` marker intervals in `flags-20260802-211200.csv` (WR-timebase stamps, UTC):

```
01:36:35.867409   dt = +0.999996585     normal, -3.48 ppm
01:36:36.867406   dt = +0.999996490     normal, -3.48 ppm
01:36:42.081211   dt = +5.213804880     <-- anomaly 1
01:36:43.867399   dt = +1.786188245     <-- anomaly 2
01:36:44.867396   dt = +0.999996430     normal, -3.48 ppm again
```

Reading of that signature:

- The two anomalous intervals sum to 6.999993 s spanning 3 true seconds (one marker
  was lost in the melee), so the net injection is +4.000000 s, matching the later
  "exactly 4.000000 s fast" era to the microsecond.
- The fractional phase returns to its pre-event value (.867406 -> .867399,
  continuing the normal -3.5 us/s slide to within ~4 us). The 10 MHz cell stream
  was never disturbed; only 4 extra PPS edges were counted. That is why re-arming
  later removed exactly 4.000000 s.
- The frequency through and after the event is unchanged: 30 s window fits on the
  short post-event tail read -3.42 to -3.54 ppm. The WR-LEN chain was in holdover
  at its last disciplined frequency, and the flip to +0.37 ppm happened only when it
  re-locked to the rebooted grandmaster, sometime between the capture end (01:39:27Z)
  and the first +0.2 ppm measurement on 08-03.

Sweep of the full file set: this is the only time-injection event.
The only other non-1 s interval (08-02 20:15:36Z, +2.999990 s) is exactly 3 nominal
seconds at -3.48 ppm: two dropped markers, no step, benign.

## The TAI arithmetic closes on the same instant

The 08-03 console check recorded the WR-LEN TAI as raw `sec:1778028404`
(the "89.03/89.04 days behind" figures in the brief were derived from this and are
imprecise at the tens-of-minutes level; use the raw value).

- Suspected boot-time `setsec` value: `1778025600` (2026-05-06 00:00:00Z).
- `1778028404 - 1778025600 = 2804 s = 46 min 44 s` of grandmaster uptime at the
  moment of the console reading.
- If the grandmaster booted at the observed PPS event (01:36:36Z), the console
  reading was taken at 02:23:20Z, or 21:23 CDT Sunday evening, minutes after the
  operator found the capture stopped (capture ended 01:39:27Z = 20:39 CDT).

Two fully independent signatures, the PPS edge burst in the capture and the TAI
counter on the WR-LEN console, point at the same reboot within the same hour.
`1778025600` being exactly midnight of 2026-05-06 suggests the init script was
written (or the unit deployed) on that date, with the then-current time frozen in
as a constant.

## Answers to the brief's three questions

### 1. Why did the offset change sign and magnitude?

Because the topology *did* change: the grandmaster rebooted at 01:36:36Z on 08-03. An
undisciplined WR master's output frequency is just its local oscillator at whatever
tune-DAC state it currently holds, and a reboot resets that state, so the free-run
frequency stepped from -3.48 ppm to +0.37 ppm. Both eras' measurements are correct.
The pre-reboot value was dead stable (hourly windows -3.4998 to -3.478, sd 0.014 ppm,
drift ~0.02 ppm across the capture, which also kills the temperature-wander candidate),
and two independent references confirm the post-reboot value. Neither measurement was
faulty.

### 2. Is the grandmaster GPS-disciplined?

No, in both eras, with high confidence from downstream evidence. A steady -3.48 ppm
before the reboot, and +0.37 ppm with a monotonically walking PPS phase after it, are
both impossible for a GPS-disciplined source, and its time resets to a hardcoded
constant at boot on top of that. What downstream evidence cannot supply is the owner
and the `init show` confirmation, which need the grandmaster itself (checklist below).
Strictly, the conclusion applies to whatever device is the time authority at the top
of the chain the two WR-LENs sync to; both report `slave_wr0`, so neither of them is
it.

### 3. Is the software mitigation still right?

The *tool* yes, the *premise* no. A fixed -3.48 ppm scale constant is wrong now and
will be wrong again after every future grandmaster reboot, because the offset belongs
to the grandmaster's current boot rather than to the installation. Recommendations:

- Never bake in a constant. Measure the offset per run from that run's own `$8F`
  markers, which `gps_calibrate.py` already does from a capture's CSVs, or use
  `to_true_ns_tracked`, which follows the wander and would also ride through a
  mid-run step.
- Fix the stale docstring in `gps_calibrate.py`. It states as established fact that
  the source is "a WR-LEN that is NOT slaved to a grandmaster" at "-3.48 ppm". The
  WR-LENs are healthy slaves, the grandmaster is the undisciplined element, and the
  number is per-boot.
- Add a reboot canary. The WR-LEN TAI, readable from its console, is a free
  grandmaster-uptime counter, so log it once per capture session. If
  `TAI - 1778025600` ever exceeds real elapsed-since-08-03 or resets, the grandmaster
  rebooted and the calibration constant died with it. The RTL `PPS_REJECT` counter
  (glitch filter, commit bac3c43) now guards the +4 s mechanism directly; check it
  whenever a capture ends and after any suspected upstream event, and re-arm.
- The 4 s incident itself is closed: the cause was the spurious-edge burst, not a
  bad arm clock (the arm-guard fix f803653 defends a different, real hazard: the
  dead RTC).

## Corrections to the brief

- "Separate finding, probably unrelated" (the 4.000000 s era): it is related, and it
  is the same event, observed directly. The two candidate causes the brief lists are
  now separated: spurious PPS edges, not a bad system clock at arm.
- "89.03 days off": the derived day counts (89.03 in the brief, 89.04 in earlier
  notes) disagree with each other and with the event time by 40 to 55 min. The raw
  datum `sec:1778028404` is exact and lands on the observed event to within the
  uncertainty of the console-reading time.
- The end of the weekend capture contains the step, so any future re-fit of it should
  either cut at 01:36:36Z or rely on the fitter's outlier rejection, and the "hourly
  sd 0.0079 ppm" claim should quote the pre-event span.

## What still needs the lab (unchanged from the brief, now sharper)

1. Find the grandmaster's owner. Ask specifically what happened at
   2026-08-03 01:36:36 UTC (20:36:36 CDT Sunday evening): a power blip, maintenance,
   or a crash? That timestamp is now exact enough to correlate with facility logs.
2. On the grandmaster: `init show` (expect `setsec 1778025600` or similar),
   `mode`, `time`, uptime. Check `CLK IN` / `PPS I/O` SMAs for anything connected.
3. Take a fresh WR-LEN TAI reading and note the wall time to the second. Predicted
   value if no further reboot: `TAI = 1778025600 + (now_utc - 2026-08-03T01:36:36Z)`,
   accurate to a few seconds. Any other value means another reboot happened and the
   +0.37 ppm figure is stale too.
4. Re-measure the frequency offset after any grandmaster event, and periodically
   regardless: a free-running oscillator's value is only valid for its boot epoch.

Analysis scripts used here (marker-interval sweep, split fits, windowed fits) were
session scratch built on `deploy/gps_calibrate.py`'s public functions; the numbers
above are reproducible with `load_marker_ns` + `calibrate` on the weekend CSVs.

## Appendix: live tracking, 2026-08-04 (board session)

Timebase armed 14:12:42.5Z (from PPS_COUNT anchor). `PPS_REJECT = 0`.
`wr_time.py status` HW-minus-system deltas, chrony healthy at the time
(RMS offset 9.7 us, skew 0.019 ppm, stratum 3 off chablis.fnal.gov):

| UTC | PPS_COUNT | HW - system |
|---|---|---|
| 15:42:28.496Z | 5386 | -0.047797 s |
| 15:45:13.784Z | 5551 | -0.047815 s |
| 15:56:55.726Z | 6253 | -0.047806 s |
| 15:57:04.219Z | 6262 | -0.047806 s |

Slope over that baseline is -0.01 +- ~0.04 ppm, so the frequency offset sits
at roughly 0 rather than the +0.37 ppm measured on 08-03/04. Read at the time as the
post-reboot crystal retrace still settling (+0.42 -> +0.37 -> +0.34 -> ~0.0).
The "someone disciplined it today" alternative predicts a corrected TAI date and an
eventually steered PPS phase; the settling alternative predicts TAI still lagging by
exactly 89 d 1:36:36. The next WR-LEN TAI reading decides it.

### Second live event, same day, 16:38-17:52Z

- 259 PPS edges went missing between 16:38:02Z and 17:51:52Z (PPS_COUNT 8719 ->
  12891 across 4430.7 wall seconds), in multiple segments; `lost_lock` latched;
  `PPS_REJECT` still 0, so the edges were absent rather than glitching.
- After the final auto re-arm the timebase ran exactly 15.9996 s slow, because the
  guard leaves an arm pending through an outage and the relock then consumes the
  stale label. Guard bug, fixed the same day in `wr_time.py` (pending-arm label
  refresh, plus a continuous label audit against the system clock, `label_error()`).
- The PPS phase stepped from 47.8 ms to 0.39 ms off the UTC boundary, after the
  frequency had already moved to ~0 ppm earlier in the day. This was read at the
  time as "someone is disciplining the grandmaster". Corrected 2026-08-05: the
  disturbances were the experimenters' own bench work. The PPS lead was being moved
  onto frequency counters and the source was reset during the investigation. The
  near-UTC phase landing was a coincidence, and a reminder not to over-read phase
  coincidences without an activity log.
- `wr-guard.log` timeline: ten outages of 17 to 42 s in two clusters,
  16:44:39-16:50:31Z and 17:01:20-17:07:12Z (total downtime ~280 s, which matches the
  259 missing edges plus detection latency), quiet afterwards. Twelve auto re-arms.
  Two mid-outage arm consumptions (attempts 5 and 11 fired while a prior arm should
  still have been pending) show the PPS flapping rather than cleanly absent. The two
  tight clusters at 11:44 and 12:01 local time read as hands-on work at the source,
  twice.
- Data impact, refined: from 16:44:39Z to the corrective re-arm, each of the ten
  locked spans carries its own offset of roughly -(15..40) s (its arm's
  staleness); the spans between them are UNSYNC and were dropped by the
  publisher, so there is no *wrong* data there, only gaps. From 17:07:12Z the
  offset is a constant -15.9996 s until corrected. Chrony was verified healthy
  through the whole window (RMS 7 us), so the labels' source clock is blameless.
- The 08-03 guard sessions (16:21Z, 21:38Z starts) show zero unlocks, so these
  are the first PPS interruptions since the 08-03 01:36:36Z reboot event.

### Post-incident anchor, 18:03:53.750Z (the current one for check-backs)

| UTC | PPS_COUNT | HW - system | notes |
|---|---|---|---|
| 17:51:52.473Z | 12891 | -15.999613 s | stale label, pre-correction |
| 18:03:53.750Z | 13596 | +0.000558 s | corrective arm applied; locked, PPS_REJECT=0, lost_lock cleared |
| 18:08:24.094Z | 13867 | +0.000614 s | +0.21 ppm vs previous row; edge count continuous |
| 18:09:31.712Z | 13934 | +0.000616 s | noise-level confirm; no outages since the corrective arm |
| 18:58:46.842Z | 16761 | -30.998721 s | third cluster 18:45:03-18:51:28Z (6 outages, attempts 13-20); stale label again, needs another corrective arm |

Walk-rate consensus late on 08-04, from three independent intervals across
17:51-18:58Z and using the mod-1 phase that rides through integer relabels:
+0.22 +- 0.02 ppm, which is +0.80 ms/h or +19 ms/day. The third cluster did not step
the phase (18:58's +1.279 ms is exactly what the walk from 18:09 predicts), unlike the
midday clusters, which stepped it 47 ms. Rate history for the day: ~0.00 ppm at
15:42-15:57Z, +0.22 ppm after ~18:45Z. The oscillator moved again during the
afternoon work.

Third cluster note: every unlocked line all day reads `pps_alive=0 clk10_alive=1`.
Only the PPS ever disappears, the 10 MHz never blinks. At the time this was read as a
WR-LEN gating its PPS on servo lock during upstream re-acquisitions. Corrected
2026-08-05: the clusters were the experimenters themselves, moving the PPS lead onto
bench frequency counters and resetting the source. That is also exactly why only the
PPS line was affected while the 10 MHz stayed connected. Lesson recorded for the next
investigation: before theorising about dropout patterns, ask who was in the room.

Wrinkles in this pair:

- PPS_COUNT advanced 705 against 721.3 wall seconds, so 16 more edges went missing
  after the 17:51 reading. That is one further ~17 s outage segment around
  17:52-18:00Z (an 11th segment; the guard log should show attempts 13+). Check
  `tail wr-guard.log` at the next session to confirm.
- Sub-second phase went from +0.387 ms (after the boundary) to -0.558 ms (before it),
  so the PPS crossed the UTC second boundary between the readings, and the implied
  frequency between them is +0.24 ppm against ~0.0 at 15:42-15:57. The source is
  still slewing or hunting; do not treat any frequency number from 2026-08-04 as
  settled.

### 2026-08-05 14:16-14:23Z live monitor (post-experiment state)

- Rate is now 0.00 +- 0.03 ppm (ppm(run) converged to -0.01), down from +0.22 the
  previous evening. Delta is parked at +4.040 ms and flat.
- PPS_REJECT reads 223,321, so about 223k spurious edges were discarded since the
  previous evening (it read 0 at 18:09Z on 08-04). The experimenters confirmed where
  they came from: their own bench work, moving the PPS onto frequency counters and
  resetting the source. Without the 08-03 glitch filter the timebase would now be
  +223,320 s wrong. Residual rejects were still arriving occasionally
  (223320 -> 223321 at 14:17:31Z).
- PPS_COUNT arithmetic: 86,275 at 14:22:56Z vs PL-load epoch 08-04 14:12:42.5Z
  gives ~739 s of cumulative missing edges since load, so roughly 460 more
  seconds of outage happened after the last session beyond the ~275 known by
  18:58Z. Relocks under the old guard mean that stretch spans unknown per-segment
  label offsets. The label at 14:16Z was correct, though: delta 4 ms, well under a
  second.
- Given that attribution, "the grandmaster got disciplined" is not established. The
  ~0.00 ppm is more likely just where the source's oscillator happens to sit after
  the team's final reset, and a fresh post-reset retrace like 08-03's may walk it
  again over the coming days. The same two confirmations still decide it: ppm(run)
  holding ~0.000 over hours, and a WR-LEN console `time` showing a correct date
  rather than 2026-05-06 plus elapsed. Open question for the team: which box was
  "the source" that got reset, the grandmaster itself or a WR-LEN? If they can reset
  the grandmaster, they already have the access the owner hunt was looking for, and
  the runbook's `mode` / `time` / `init show` can be run today.

To evaluate the next status reading:
`ppm_avg = (delta_new - 0.000558 s) / seconds_since_18:03:53.750Z * 1e6`.
`PPS_COUNT_new - 13596` against elapsed wall seconds counts any further missing edges.
A delta that has suddenly gone tens of seconds negative means another outage was
relocked by the old on-board guard with a stale label: fix it with one `arm`, and
deploy the fixed `wr_time.py` (label refresh plus the `label_error` audit).

### 2026-08-05 15:34Z: the TAI reading. Closure.

Timed console reading on WR-LEN `_1595`: at wall 15:34:17Z, `time` returned
**2026-05-07 01:39:30.456**. Elapsed past the `setsec` epoch (1778025600 =
2026-05-06 00:00:00Z) is 1 d 1:39:30, so the time authority's last boot was
**2026-08-04 13:54:47Z = 08:54:47 CDT Monday morning**, ±1 s: inside the team's
Monday setup window, ~18 min before the PL reload and arm at 14:12Z.

- **Free-running confirmed at the source level.** TAI = hardcoded setsec +
  uptime, now directly observed across two independent boots (08-03 01:36:36Z
  and 08-04 13:54:47Z). Not disciplined as of 2026-08-05: today's flat ~0.00 ppm
  is a free-run dwell, not a lock.
- **The box the team resets IS the time authority** (pending their confirmation
  of the 08:55 CDT Monday timing), which answers the brief's "who owns it"
  question operationally: the experimenters have hands-on access, and the
  runbook's `mode` / `init show` steps can be run at any bench session.
- Every frequency era maps to a reset: -3.48 ppm until 08-03 01:36:36Z,
  +0.42/+0.37/+0.34 after it, ~0.0 ± 0.05 meander after 08-04 13:54:47Z. The
  post-reset tune point is effectively random at the sub-ppm scale, so per-run
  calibration is permanent policy until a real reference is attached.
- Cross-checks agree: servo `ucnt` (~16.1 h of tracking, so the LEN's servo last
  re-acquired ~23:20Z on 08-04, after the GM boot, as required), the 08-04
  midday ~0.00 ppm (first measurement after that morning's reset), and the
  08-03 console reading (2804 s past epoch then = the Sunday boot).
