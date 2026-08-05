# White Rabbit reference investigation: brief for a fresh analyst

Status as of 2026-08-04. Two independent investigations have been merged here, and
they disagree on the headline number. Resolving that disagreement is the primary
task.

---

## What I want you to investigate

**1. Why did the frequency offset change sign and magnitude between 2026-07-31 and
2026-08-04, while nothing in the topology apparently changed?**

That is the central puzzle. Everything else is context.

**2. Is the grandmaster GPS-disciplined, and if not, who owns it?** The evidence says
no. Confirm it at the source and find out whether that is intentional.

**3. Given (1) and (2), is the existing software mitigation still the right approach?**
It was designed against a number that may no longer be correct.

---

## The system

```
[ grandmaster, owner UNKNOWN ]
        |  ~137 m fibre
        v
   wr0 [ WR-LEN _1597 ] wr1
        |  1.5 m
        v
   wr0 [ WR-LEN _1595 ]
        |  coax, AC-coupled
        v
   KR260 FPGA  (10 MHz + PPS on Pmod E10 / E12)
```

The KR260 builds a `{sec, ns}` timebase in `rtl/wr_timebase.sv`. `sec` is loaded ONCE
from the board's Linux clock when armed, then incremented by +1 per PPS edge forever.
`ns` comes from counting 10 MHz cells with non-accumulating interpolation between them.

**A hardware second is therefore exactly one PPS period by construction.** The timebase
inherits the PPS source's frequency exactly, and the Linux clock is read exactly once
and never again. This matters because it rules out whole classes of explanation.

---

## The disagreement to resolve

Frequency offset of the WR-derived clock, all measurements against the same hardware:

| when | reference | result | fit residual |
|---|---|---|---|
| 2026-07-31 | TCLK `$8F` | **-3.48092 ppm** | 190 µs sd |
| 2026-08-03 | TCLK `$8F` | +0.201 ppm | 217 µs sd |
| 2026-08-03/04 | NTP (chrony) | +0.422 ppm | n/a |
| 2026-08-03/04 | NTP (chrony) | +0.421 ppm | n/a |
| 2026-08-03/04 | NTP (chrony) | **+0.366 ppm** | n/a |
| 2026-08-04 | TCLK `$8F` | **+0.343 ppm** | 473 µs sd |

The 07-31 measurement was **very stable**: hourly windows gave sd 0.0079 ppm, ptp
0.0297 ppm. It does not look like a bad fit. It looks like a real, steady -3.48 ppm.

The recent measurements are **mutually consistent across two independent references**.
NTP (chrony, stratum 3 off `chablis.fnal.gov`, 21 µs RMS, ~5 µs current offset) and
TCLK `$8F` now agree to within 0.02 ppm over long spans.

So both eras look internally solid and they differ by 3.8 ppm including a sign flip.
Candidate explanations, none confirmed:

- the grandmaster was changed, rebooted, or re-disciplined between the two dates
- a free-running oscillator wandering with temperature (3.8 ppm is large but not
  impossible for an undisciplined crystal across a wide swing)
- something wrong with the 07-31 measurement that the low residual hides
- the two eras measured different physical paths (verify the topology was identical)

**Deciding between these is the job.** Prefer evidence from the grandmaster itself over
further re-analysis of the captures.

---

## Verified facts

### The grandmaster is almost certainly not GPS-disciplined

Both WR-LENs report `mode = slave_wr0`, `WR Slave Locked Calibrated`,
`Servo state: TRACK_PHASE`, `Clock offset: 1 ps`. Neither is the grandmaster, and both
hops are healthy.

**The decisive evidence, independent of TCLK entirely: both units report their TAI clock
as 2026-05-06 while the real date is 2026-08-04.** That is 89.03 days off, and the two
units agree with each other to the second. WR is PTP-based and distributes absolute
time, so slaves inherit the grandmaster's time. A GPS-disciplined grandmaster cannot be
89 days wrong.

Suspected but unverified: a hardcoded `setsec` in the grandmaster's EEPROM init script.
`1778025600` is exactly `2026-05-06 00:00:00`. Check with `init show`.

### The PPS phase is walking, which is independent confirmation

Measured on the KR260 against NTP:

- the PPS edge sits **67 ms** away from the UTC second boundary (later `-47 ms` after a
  re-arm, since the label changed but the phase did not)
- that phase **walks monotonically at the same 0.42 ppm**, about 1.3 ms/hour, which
  would take ~32 days to traverse a full second

A disciplined source holds phase indefinitely. This is the same offset seen as phase
rather than rate, and it does not depend on TCLK or `$8F` at all.

### The PPS edge count is perfect

- `CELLS_LAST` read exactly 10,000,000 on every interval
- over a separate window, `PPS_COUNT` advanced exactly in step with wall seconds,
  not one extra or missing pulse
- `PPS_REJECT` (a new glitch filter, see below) reads 0

The 10 MHz and the PPS are coherent, which is expected since the PPS is the 10 MHz
divided down. **Note this cannot distinguish disciplined from free-running**: both
signals share the same oscillator error, so it cancels perfectly in that measurement.
Only an external reference can see it.

---

## Ruled out. Do not re-investigate.

- **The WR-LENs.** 1 ps offsets on both hops, both locked and calibrated.
- **Lost or extra edges.** See the counts above. A dropped 10 MHz edge reads `<1e7`, a
  dropped PPS reads `2e7`. Neither occurred.
- **A dropped edge as the mechanism for the ppm offset.** It produces a 1 second step,
  not a slope. Faking -3.48 ppm needs 0.66 lost edges across the run, and the fit
  residual is 190 µs sd.
- **The AC-coupling network** (10 nF series, 140 Ω to 3.3 V, 92 Ω to GND; Thevenin
  1.309 V / 55.5 Ω, τ 555 ns). Passive networks conserve edge count. They add delay and
  jitter, never frequency.
- **The FPGA.** See the "by construction" argument above.

---

## Corrections to earlier claims

The following appeared in the earlier investigation and are **stale or wrong** as of
2026-08-04. Do not carry them forward.

**"chrony isn't disciplining, the board's Linux clock is ~-21 ppm."** Chrony is working:
stratum 3 off `chablis.fnal.gov`, `System time 0.000004709 seconds fast of NTP`, RMS
offset 21 µs, and `chronyc waitsync 60 0.1` returns on the first try with a 3 ns
correction. Chrony reports the local oscillator as `2.192 ppm fast`, which it corrects.
The system clock is now good to microseconds and is trustworthy as a reference.

**"The PPS source is free-running"** was written into `gps_calibrate.py`'s docstring as
established fact, on the basis of the -3.48 ppm figure. Given the disagreement above,
treat that module's premise as **unverified** until (1) is resolved.

A separate hazard was found instead: **the board's RTC is dead** (`timedatectl` reports
`RTC time: Thu 1970-01-01`), so every boot starts ~58 days wrong until chrony steps it:

```
Aug 3 01:46:01  System clock was stepped by 5047494.677722 seconds
Aug 3 12:31:19  System clock was stepped by 5086002.156533 seconds
```

Since `sec` is loaded from that clock at arm, arming inside that window labels the
timebase weeks off, invisibly. This is a board problem, not a WR problem, and it is now
guarded in software.

---

## Reference clock rules

`$8F` is TCLK's GPS-locked 1 Hz marker and the only absolute reference in the timing
chain. **Never use `$02` for frequency**: it is a supercycle marker whose period
genuinely varies (reads -3.89 ppm, with 2 s excursions in its residual).

Treat `$8F` with some caution over short spans. Its fit residual is 190 to 473 µs
depending on the capture, dominated by physical marker delivery jitter, by ~512-event
stale-FIFO blocks at each capture restart, and by gaps in the archive. It agrees well
with NTP on the later captures. It disagreed sharply on the 07-31 one, which is
exactly what needs explaining.

---

## Separate finding, probably unrelated but do not assume

On 2026-08-03 the published timestamps ran **exactly 4.000000 s fast**. Re-arming
removed exactly 4.000000 s. `wr-guard.log` showed no re-arms and no lock loss, and a
later window showed a perfectly clean PPS.

Two candidate causes remain and were not separated: a transient burst of spurious PPS
edges, or an arm against a system clock that was briefly wrong. Both are now defended
against (an RTL glitch filter with a `PPS_REJECT` counter, and a pre-arm clock guard),
and `PPS_REJECT` will distinguish them if it recurs.

Mentioned because a 4 second whole-number offset and an 89 day grandmaster offset are
both absolute-time errors, and it is worth a moment's thought whether they share a
cause. Current belief is that they do not.

---

## What to do at the grandmaster

This is the real fix and everything else is mitigation.

1. Find its owner.
2. On it, run `init show`, `mode`, `time`.
3. Check whether anything is physically wired to its `CLK IN` and `PPS I/O` SMAs.
4. If `init show` contains a hardcoded `setsec`, that confirms the suspicion and
   explains the 89 days, though **not necessarily the frequency offset**, which is a
   separate property. Distinguish these two clearly in whatever you report.

---

## Existing mitigation

`deploy/gps_calibrate.py` plus tests, on branch `tclk-subsample-fine-timing`.

| method | residual sd |
|---|---|
| raw | 189.8 ms |
| single scale factor | 190.4 µs |
| tracked, 1 h smoothing | 8.2 µs |

Important limitation, stated in the module itself: this is a **duration** correction
computed from marker intervals. It rescales elapsed time and cannot recover an absolute
epoch offset. It is deliberately not in the publish path, so published timestamps are
raw hardware stamps.

If (1) resolves to "the reference genuinely changed", a fixed scale factor is the wrong
shape of fix and the tracked variant or a re-measurement per run becomes necessary.

---

## Operational note

The PL was reloaded on 2026-08-04, which clears the `$07` drop mask. It is re-applied
automatically by `redis_publish.py --drop 07` at launch. If a capture is started by some
other path, re-apply it or the event rate goes from ~98 to ~820 ev/s and the FIFO
overflows.

---

## What a good answer looks like

- a definite statement on whether the grandmaster is disciplined, with evidence from the
  grandmaster itself rather than inference from downstream
- an explanation for the -3.48 to +0.37 ppm change that accounts for BOTH eras' data,
  or a specific reason to distrust one of them
- a recommendation on whether the software mitigation should be kept, re-parameterised,
  or removed

Say plainly if the data cannot settle something. Two theories were already advanced and
refuted during this investigation by exactly the sort of arithmetic that should be done
before proposing a mechanism.
