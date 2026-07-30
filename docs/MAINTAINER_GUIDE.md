# Repository maintainer guide

This is the technical handoff document for `kria-2-hardware`. It explains the
product, the code paths that implement it, how the programmable logic and Linux
software interact, how to build and test changes, and which constraints were
learned through hardware bring-up.

For procedures while operating the board, use [OPERATIONS.md](OPERATIONS.md).
For exact port and register bit fields, use the
[hardware interface guide](generated/tclk-aclk-pipeline-hardware-interface-guide.md).

## 1. What this repository produces

The repository produces one supported KR260 FPGA design, `aclk_pipeline`, and
the board-side programs needed to capture and distribute its data.

The programmable logic performs this chain:

```text
Fermilab TCLK input
  -> recover and decode event bytes
  -> timestamp every event against White Rabbit 10 MHz + PPS
  -> FIFO/AXI readout to the processing system
  -> re-encode the decoded events as gigabit ACLK
  -> transmit through the KR260 SFP+ cage
  -> external fiber loop from TX back to RX
  -> receive, align, CRC-check, and decode ACLK
  -> timestamp the recovered event on the same WR timeline
  -> second FIFO/AXI readout to the processing system
  -> mirror the recovered event as ACLK-Lite Manchester on a Pmod pin
```

Linux opens three UIO devices: one TCLK readout, one ACLK readout, and one White
Rabbit control/monitor block. Two publisher processes drain the event FIFOs and
write Redis Streams conforming to the Fermilab RedisAdapter Protocol v1.0. A WR
guard restores lock after reference interruptions, a statistics ledger accounts
for every event, and a separate archiver copies retained stream entries to CSV.

The system is therefore both a timing receiver and a self-contained transport
test: one board receives real TCLK, republishes it through ACLK and ACLK-Lite,
and compares the input and looped-back output on a shared absolute timeline.

## 2. Current validation status

The integrated system completed a 15.6-hour dual-source hardware capture on
2026-07-16 with approximately 5.55 million events per source and zero measured
loss. That run exercised real TCLK, WR lock/timestamping, TCLK-to-ACLK encoding,
the optical SFP loop, ACLK decoding, both readout FIFOs, and Redis publication.

The main unresolved qualification is external ACLK interoperability. The ACLK
receiver has been exercised with the board's own encoder through real optics,
not with a live upstream accelerator ACLK transmitter. The CRC-8 convention and
full interoperability should be confirmed against such a source.

The Pmod/SFP constraints are known working inputs to the current project, but the
repository explicitly treats physical package-pin values as items to confirm
against the official KR260 master XDC and carrier schematic before new adapters
or hardware variants are connected.

## 3. How to decide what is authoritative

When documentation and implementation differ, use this order:

1. Checked-in implementation and tests under `rtl/`, `deploy/`, `vivado/`,
   `constraints/`, and `tb/`.
2. This guide, [PROJECT.md](PROJECT.md), [OPERATIONS.md](OPERATIONS.md), and the
   generated hardware interface guide.
3. Focused references such as [aclk-lite-framing.md](aclk-lite-framing.md),
   `deploy/wr.md`, and `deploy/redis.md`.
4. `docs/superpowers/specs/` and `docs/superpowers/plans/`. These preserve design
   intent and implementation history, but a plan can describe work that was
   intermediate, superseded, or not yet completed.
5. PDFs in `resources/`, which are upstream/background references rather than a
   literal description of this implementation.

The definitive list of files synthesized into hardware is in
`vivado/build_aclk_pipeline.tcl`. Not every RTL file is in the production build.

## 4. Repository layout

| Path | Responsibility |
|---|---|
| `rtl/` | Synthesizable timing-link, timestamp, readout, CDC, and integration RTL |
| `rtl/aclk_bridge/` | Fermilab TCLK/ACLK receive logic, CRC, and gearboxes |
| `rtl/aclk_lite/` | TCLK readout top, ACLK-Lite encoder, and simulation support decoder |
| `rtl/aclk_gt/` | TCLK-to-ACLK encoder and GT ACLK readout wrapper |
| `rtl/aclk_readout/` | Shared event FIFO core and AXI4-Lite register interface |
| `tb/` | Cocotb tests, HDL harnesses, bus helpers, and link stimulus models |
| `vivado/` | Scripted block-design build and committed GT Wizard IP |
| `constraints/` | KR260 physical and timing constraints |
| `deploy/` | Board load artifacts, readers, publisher, Redis tools, reports, and plots |
| `docs/` | Architecture, operations, protocol details, and design records |
| `resources/` | Fermilab TCLK/ACLK and PIP-II source material |
| `sim.ps1`, `sim.sh` | Simulation setup/run/wave/scaffold wrappers |
| `hw.ps1`, `hw.sh` | Vivado build/package/deploy/GUI wrappers |
| `requirements.txt` | PC-side simulation and test packages |
| `deploy/requirements-board.txt` | Board runtime Python dependencies |

`.venv/`, `.Xil/`, `build/`, `sim_build/`, and pytest caches are generated state.
Rebuild from the scripted flow instead of treating an old Vivado project as source.

## 5. Hardware architecture

### 5.1 Integrated top and block design

`rtl/aclk_pipeline_bd_top.v` is the production RTL integration point. Vivado
instantiates it as a module-reference cell from `vivado/build_aclk_pipeline.tcl`.
The TCL also creates:

- the Zynq UltraScale+ processing-system block;
- PS PL clocks and reset handling;
- 80 MHz, 200 MHz, and 50 MHz derived clocks;
- a three-output AXI SmartConnect;
- the external TCLK, WR, SFP, GT reference-clock, and debug ports;
- the three AXI address ranges; and
- synthesis, implementation, bitstream, and routed timing reporting.

The block design retains the historical name `uart_echo_bd`. Consequently the
wrapper and loadable bitstream are named `uart_echo_bd_wrapper.bit` and
`uart_echo_bd_wrapper.bit.bin`. The name is cosmetic and retained to preserve the
existing device-tree/UIO identity.

### 5.2 Clocks and resets

| Domain | Current rate | Main users |
|---|---:|---|
| `s_axi_aclk` / `pl_clk0` | approximately 100 MHz | AXI interfaces and WR monitor |
| `clk_80m` | 80 MHz | TCLK line oversampling and ACLK-Lite output |
| `clk_40m` | 200 MHz | TCLK deserializer/readout and 5 ns time interpolation |
| `tx_usrclk2` | GT-generated | ACLK transmission |
| `rx_usrclk2` | GT-generated | ACLK receive/decode/readout |
| `freerun_50` | 50 MHz | GT reset controller |
| GT refclk | 156.25 MHz differential | 1.25 Gb/s GTH link |

`clk_40m` is a legacy signal name. In the current build it is 200 MHz. Do not
derive timeout or timestamp assumptions from its name.

The 80 MHz raw TCLK sampler is intentional. Hardware trials found that much
faster sampling interpreted analog ringing and slow edges as extra transitions.
Only the downstream deserializer/timestamp domain was raised to 200 MHz. The two
clocks are decoupled rather than maintained at a fixed ratio.

GT receive and transmit resets assert asynchronously and deassert synchronously
inside their respective user-clock domains. ACLK decoder recovery does not reset
the readout FIFO pointers. The block design's SmartConnect and reset topology,
including the proven `dcm_locked` tie-off, are hardware-derived constraints and
should not be replaced casually with Vivado automation defaults.

### 5.3 Clock-domain crossing primitives

The common CDC modules are:

- `synchronizer.sv`: multi-stage synchronization of asynchronous level signals;
- `cdc_word_pulse.sv`: toggle-handshake transfer of a word plus a destination pulse;
- `cdc_gray_count.sv`: gray-coded telemetry counter transfer; and
- `async_fifo.sv`: ordered event transfer between receiver and AXI domains.

CDC behavior includes reset behavior. Any change should be exercised with the
dedicated CDC/FIFO tests as well as the relevant integrated test.

### 5.4 TCLK receive path

`rtl/aclk_lite/tclk_readout_top.sv` owns the single production TCLK decoder:

1. `serdec4_9MHz.v` samples the biphase-mark line and recovers cells.
2. `TCLK_DESERIALIZER2.v` locates and assembles the serial frame.
3. `TCLK_RCV.v` emits an 8-bit event and parity/error indications.
4. `aclk_readout_core.sv` stamps valid events and writes an asynchronous FIFO.
5. `aclk_readout_axi.sv` exposes the FIFO and diagnostics to Linux.

The top exports a decoded event/data-valid tap to `aclk_tclk_encoder.v`. There is
not a second TCLK decoder for the transmit path.

### 5.5 White Rabbit timebase

`rtl/wr_timebase.sv` creates a packed `{seconds[31:0], nanoseconds[31:0]}` value
from asynchronous WR 10 MHz and PPS signals. At each 10 MHz edge the nanosecond
base advances by 100 ns; between edges a fixed-point interpolator advances at the
local clock period. At PPS, nanoseconds reset and seconds load or increment.

Linux supplies the absolute seconds label. `wr_time.py arm` waits for a safe
mid-second interval and writes `floor(system_time)+1`, the label for the next PPS.
The request crosses through `cdc_word_pulse` and all timebase replicas consume it
at the boundary.

The validity contract is strict:

- before a successful arm, timestamp output is zero;
- missing WR 10 MHz or PPS activity clears lock;
- a reset/relock of the ACLK receive domain clears that replica's lock;
- returning reference signals do not automatically restore absolute time; and
- software interprets timestamp zero as `UNSYNC` and does not publish it.

There are three timebase instances: TCLK-domain, ACLK-domain, and an AXI monitor
inside `wr_timebase_axi.sv`. The monitor fans arm/disarm requests to both event
domains and reports all three locks, reference activity, PPS count, measured
10 MHz cells per interval, current time, and a sticky lost-lock flag.

The PPS pulse must be wide enough for the local sampler. In the deployed setup it
must be roughly 100 ns or wider; a native 10 ns WR PPS can be missed entirely.

### 5.6 Shared FIFO and AXI readout

Both TCLK and ACLK use:

- `aclk_readout_core.sv` for event packing, timestamp capture, null suppression,
  counters, and the receive-to-AXI async FIFO; and
- `aclk_readout_axi.sv` for the software-visible FIFO head, counters, event filter,
  telemetry, and optional GT control.

Software reads a stable FIFO head in this order: EVENT, optional DATA, timestamp,
then POP. A write to POP advances the head. Running two consumers against one UIO
readout is unsafe because both mutate the same FIFO.

Registers are spaced **16 bytes apart**. The hand-written module-reference slave
returned zero for non-16-byte-aligned offsets on the KR260 LPD path. Every future
register must remain on that grid, with matching RTL, Python, tests, and docs.

| Offset | Register | Meaning |
|---:|---|---|
| `0x00` | STATUS | FIFO empty and sticky overflow |
| `0x10` | EVENT | flags and event code at the FIFO head |
| `0x20`, `0x30` | DATA_HI/LO | optional 64-bit payload |
| `0x40`, `0x50` | TS_HI/LO | packed 64-bit timestamp |
| `0x60` | POP | any single write consumes the head |
| `0x70` | EVENT_COUNT | accepted decoded events |
| `0x80` | NULL_COUNT | suppressed null events |
| `0x90` | ERROR_COUNT | parity/CRC/decode failures |
| `0xA0` | DEBUG | source activity or GT health |
| `0xB0` | HEARTBEAT | receive-domain activity/trust check |
| `0xC0` | LOCK | decoder/timebase lock indication |
| `0xD0` | FILTER_CFG | event-code drop-mask update |
| `0xE0` | FILTERED_COUNT | events dropped by the PL filter |
| `0xF0` | GT_CTRL | ACLK-only polarity, loopback, TX drive, and resets |

The full bit definitions and WR register map are in the hardware interface guide.

### 5.7 ACLK transmit, receive, and recovery

`rtl/aclk_gt/aclk_tclk_encoder.v` converts decoded TCLK events into ACLK frames,
using the CRC and 96/16-bit gearbox logic, and drives 16-bit data plus K-character
flags into the GT Wizard core.

`vivado/ip/aclkgt_gt/aclkgt_gt.xci` is committed generated IP configured for GTH,
1.25 Gb/s, 8b/10b, a 156.25 MHz reference, and K28.5 comma alignment. If it must
be regenerated, use `vivado/ip/gen_aclkgt_gt.tcl`. That script validates generated
netlist and port properties because Vivado configuration readback has previously
reported settings that were not actually emitted.

The current system requires an external optical jumper from the SFP+ TX back to
RX. `ACLK_REV.v` (`ACLK_RCV`), the receive gearbox, and `crc8_calc.v` align and
decode the returned frames. `aclk_gt_readout_top.sv` wraps this with the second
WR-stamped readout.

The integrated top has SEARCH, LOCKED, and RECOVER states. Comma alignment remains
enabled while searching and is held after decoder lock. Sustained byte-alignment
loss causes local decoder recovery. GT disparity, not-in-table, comma, elastic
buffer, recovery, link, and SFP-sideband signals are summarized in DEBUG. GT_CTRL
allows controlled polarity, loopback, TX swing/emphasis, and reset experiments.

### 5.8 ACLK-Lite mirror

The one ACLK decoder exports a decoded-event tap to `aclk_lite_bridge.v`, which
adapts it to `aclk_lite_encoder.sv`. The encoder produces the biphase-mark output
on Pmod B10. Nulls are suppressed and back-to-back events remain separate. The
authoritative byte/frame rules are in
[aclk-lite-framing.md](aclk-lite-framing.md).

`rtl/aclk_lite/clk_rcv.sv` and `clk_byte_framer.sv` support simulation. They are
not included by the production Vivado source list.

## 6. Build, addresses, and deployment

The supported Windows build command is:

```powershell
.\hw.ps1 build
```

If execution policy blocks it:

```powershell
powershell -ExecutionPolicy Bypass -File .\hw.ps1 build
```

The wrapper locates Vivado, invokes `vivado/build_aclk_pipeline.tcl`, packages the
`.bit` with `bootgen`, computes MD5/SHA256, and writes `build-manifest.json`. Build
products normally land under `build/kria/aclk_pipeline/`. Keep the path free of
spaces because Vivado IP Integrator is sensitive to them.

The processing system reaches three AXI4-Lite interfaces through one SmartConnect:

| Address | Interface | Function |
|---:|---|---|
| `0x8000_0000` | `S_AXI` | TCLK event readout |
| `0x8001_0000` | `S_AXI2` | ACLK event readout |
| `0x8002_0000` | `S_AXI3` | WR timebase monitor/control |

The SmartConnect is intentional: a prior automatic interconnect/protocol-converter
path corrupted AXI4-to-AXI4-Lite read data on hardware.

Vivado producing a bitstream is not proof of timing closure. The build TCL emits
routed WNS and worst setup paths. Require WNS greater than or equal to zero and
inspect the binding domain after clock or datapath changes.

Deployment uses `deploy/aclk_pipeline.dts`, compiled to a device-tree overlay.
The normal board flow is:

```bash
dtc -@ -O dtb -o aclk_pipeline.dtbo aclk_pipeline.dts
sudo xmutil unloadapp
sudo fpgautil -b uart_echo_bd_wrapper.bit.bin -o aclk_pipeline.dtbo
grep . /sys/class/uio/uio*/name
```

The overlay creates UIO devices and releases PL reset. `fpgautil -f Full` does
not provide the equivalent UIO path and should not replace the overlay load.
Compare the board-side artifact hash to `build-manifest.json` before debugging.

## 7. Board-side software

### 7.1 UIO register access

`deploy/readout_common.py` owns the register map, device mapping, event read order,
startup probe, counters, filtering, and drain loops. `/dev/uioN` maps at offset
zero; `/dev/mem` is a root-only fallback using the physical base.

`RegIO` accesses cached `ctypes.c_uint32` views. This is a data-integrity
requirement: Python mmap slice assignment passed through glibc `memcpy` and issued
multiple AXI stores for one apparent four-byte write. When used on POP, it consumed
multiple FIFO entries. `RegIO.pulse()` guarantees the one device store required
for one pop. Do not replace it with slices or generic buffer-copy code.

A watchdog reports any register operation blocked for more than two seconds. A
wedged AXI CPU load usually indicates reset, overlay, bitstream, or address trouble
and may not respond to Ctrl-C.

`drain_events()` empties all available entries in bounded batches and only sleeps
when the FIFO is empty. This shape keeps the service rate above event arrival and
still lets periodic bookkeeping run during overload. Avoid adding formatting,
disk, or network work to this hot path without measuring board throughput.

### 7.2 White Rabbit control

`deploy/wr_time.py` supplies:

```bash
sudo python3 wr_time.py /dev/uio6 status
sudo python3 wr_time.py /dev/uio6 arm
sudo python3 wr_time.py /dev/uio6 disarm
sudo python3 wr_time.py /dev/uio6 clear
sudo python3 wr_time.py /dev/uio6 guard
```

`status` reports all locks, reference activity, cells per PPS interval, current
hardware UTC, and the system-clock delta. `guard` polls indefinitely and re-arms
after a WR dropout or ACLK-domain relock, logging transitions to `wr-guard.log`.
Because it derives the seconds label from Linux, chrony/systemd time sync must be
healthy.

### 7.3 Interactive readers

`tclk_read.py` and `aclk_read.py` are diagnostic console readers. They report raw
events and line/decoder health. They consume the same FIFOs as the publishers, so
never run an interactive reader and publisher on the same UIO node simultaneously.

### 7.4 RedisAdapter publisher

`redis_publish.py` drains one UIO source. It rejects UNSYNC timestamps, converts
each event to a RedisAdapter record, and submits it to `RedisSink`. `redis_sink.py`
runs a background writer so Redis latency cannot directly block the hardware FIFO.
The queue is bounded and drops the oldest item when full, with separate counters
for queue loss, Redis errors, reconnects, queued records, and published records.

The default base key is `KR260`, braced as a Redis Cluster hash tag. The primary
streams are:

```text
{KR260}:tclk
{KR260}:aclk
```

Each entry follows RedisAdapter Protocol v1.0:

- The stream ID encodes RA_Time, nanoseconds since Unix epoch, as
  `<milliseconds>-<nanoseconds-within-millisecond>`.
- The mandatory `_` field is a 15-byte little-endian `<IIIHB>` structure:
  seconds `u32`, nanoseconds `u32`, data `u32`, event `u16`, flags `u8`.
- Flag bit 0 is `has_data`; bit 1 is `is_tclk`.
- Readable `sec`, `ns`, `event`, `data`, `is_tclk`, `has_data`, and `src` fields
  are included for local tools but are not required by a generic RA consumer.

Explicit complete stream IDs work with Redis 6 and later. If two events have the
same timestamp, or WR re-arms backward, the sink advances the stream ID by one
nanosecond to satisfy Redis's increasing-ID rule. The `_` payload retains the true
hardware seconds and nanoseconds.

Per-code hashes use `{KR260}:event:<src>:0x<CODE>`. They retain the latest event
and exact count. Updates are aggregated by writer batch to reduce redis-py command
construction overhead. `{KR260}:watchdog` is the authoritative TTL liveness key;
`{KR260}:status` is sticky and insufficient by itself.

`ra_consumer.py` is reference consumer code that reads only the braced key,
RA_Time stream ID, and binary `_` field. `test_ra_roundtrip.py` can start a private
Redis server, publish through the real producer/sink path, and decode through this
consumer. It skips if `redis-server` or redis-py is unavailable.

### 7.5 Capture orchestration and accounting

`run_pipeline.sh` performs Redis and WR-lock preflight and creates a detached tmux
session containing:

- a TCLK publisher;
- an ACLK publisher;
- the WR lock guard; and
- an optional Redis-to-CSV archiver.

The important environment variables are:

- `DROP`: comma-separated hexadecimal PL event drop mask, default `07`;
- `ARCHIVE`: non-empty enables archiving, enabled by default; and
- `FORCE=1`: bypasses the full-WR-lock launch refusal for deliberate tests.

Drop-mask bits live in PL and persist across publisher restarts. Setting `DROP=""`
later does not undo bits set earlier; explicitly clear them or reload the PL.

`stats_log.py` appends periodic JSON snapshots combining hardware counters and
publisher/sink counters. `stats_report.py` detects restarts, selects the most
recent run, and reconciles:

```text
decoded = published + missed + queued + unsync
```

It distinguishes FIFO overflow, queue drops, Redis drops, pending shutdown data,
decoder errors, filtered/null events, and WR lock loss. `plot_stats.py` produces
health plots from the same logs.

`stream_archive.py` reads Redis Streams in XRANGE batches, rotates daily
`events-<src>-YYYYMMDD.csv` files, and persists resume IDs in
`archive-state.json`. It is deliberately separate from UIO access. Redis streams
are capped and intended as recent in-memory data; multi-day analysis depends on
the archive. `supercycle_plot.py` folds archived events around accelerator cycle
anchors and generates distribution/raster plots.

## 8. Simulation and verification

### 8.1 Cocotb simulation

The default fast loop is cocotb 2.0.1 with Icarus Verilog from OSS CAD Suite:

```powershell
powershell -ExecutionPolicy Bypass -File .\sim.ps1 setup
powershell -ExecutionPolicy Bypass -File .\sim.ps1 list
powershell -ExecutionPolicy Bypass -File .\sim.ps1 run -Module aclk_pipeline_chain
```

Bash equivalents are `./sim.sh setup`, `./sim.sh list`, and
`./sim.sh run -m aclk_pipeline_chain`. `test` runs then opens a waveform, `wave`
opens the most recent waveform, `new` scaffolds a module/test, and `clean` removes
simulation output. Set `OSS_CAD_SUITE` if its `bin` and `lib` are not on PATH.

Each testbench directory generally contains a `runner.py`, a cocotb
`test_<name>.py`, and sometimes an HDL wrapper. `tb/runner_common.py` centralizes
simulator invocation. Shared models generate TCLK, ACLK, ACLK-Lite, WR, and
AXI-Lite traffic. Results and plots go under `sim_build/<module>/`.

Coverage is organized around:

- CDC and storage: `synchronizer`, `cdc_word_pulse`, `async_fifo`;
- TCLK: `tclk_rcv`, `tclk_readout`;
- ACLK: `aclk_rcv`, `aclk_tclk_encoder_loop`, `aclkgt_readout`;
- readout: `aclk_readout`, `aclk_readout_ext_ts`, `aclk_readout_axi`;
- White Rabbit: `wr_timebase`, `wr_timebase_200`, `wr_timebase_axi`;
- ACLK-Lite: `aclk_lite_encoder`, `aclk_lite_bridge`; and
- integrated logical flow: `aclk_pipeline_chain`.

The end-to-end simulation does not replace vendor GT timing/analog behavior or a
routed timing report. Verification has three distinct layers: cocotb logic tests,
Vivado timing/implementation, and real hardware capture.

### 8.2 Board-side Python tests

Run from the repository root:

```powershell
python -m pytest deploy -q
```

The suite covers register spacing and single-pop behavior, event decoding, drop
filters, WR helpers, RedisAdapter record construction/decoding, queue overflow,
Redis reconnection, monotonic RA_Time IDs, statistics reconciliation, archive
resume/rotation, and plotting. The Redis round-trip integration tests run when a
local `redis-server` and redis-py are available and otherwise skip cleanly.

### 8.3 Hardware acceptance after a change

After loading a newly built image:

1. Verify its hash against `build-manifest.json`.
2. Map `/sys/class/uio/uio*/name` to actual device numbers.
3. Confirm WR `pps_alive`, `clk10_alive`, and approximately 10,000,000 cells/PPS.
4. Arm and require all three lock bits.
5. Confirm TCLK heartbeat, decoding, and error counters.
6. Confirm SFP TX/RX, GT alignment, and ACLK event decoding.
7. Run a short dual publisher capture.
8. Stop cleanly and require the statistics ledger to reconcile.
9. Only then begin a long capture or claim a regression is fixed.

## 9. Safe change workflows

### RTL, clock, reset, or CDC changes

1. Identify every affected domain and reset sequence.
2. Add or update the smallest focused cocotb test.
3. Run the focused test and `aclk_pipeline_chain`.
4. Update any affected HDL wrapper, Vivado source list, AXI software constants,
   device-tree assumptions, and documentation.
5. Build and inspect routed WNS, not just Vivado's exit code.
6. Perform the short hardware acceptance sequence above.

### Register changes

1. Allocate a 16-byte-aligned offset.
2. Implement read/write/reset semantics in RTL.
3. Preserve independent AXI AW and W acceptance; this avoids channel deadlock.
4. Update `readout_common.py` or `wr_time.py` constants and names.
5. Add AXI tests, Python access tests, and documentation.
6. Verify write-sensitive registers receive exactly one physical store.

### Redis or consumer contract changes

Treat the braced keys, RA_Time IDs, binary payload layout, flag meanings, readable
fields, liveness keys, and per-code hashes as an external API.

1. Update producer and reference-consumer unit tests.
2. Run the real RedisAdapter round-trip test when Redis is available.
3. Check archive, reports, plots, and downstream consumer compatibility.
4. Benchmark on the KR260: added per-event packing, formatting, or Redis commands
   can lower drain throughput enough to overflow hardware.
5. Update `deploy/redis.md` and this guide together.

### GT/IP or physical-interface changes

1. Regenerate GT IP through the checked-in TCL, never only through unrecorded GUI
   changes.
2. Review `.xci`, wrapper, optional ports, equalizer, buffer, comma, and 8b/10b
   differences.
3. Recheck package pins, voltage, direction, reference-clock site, and SFP sidebands
   against official hardware sources.
4. Separate internal/near-end loopback results from real optical-link results.
5. Verify both link recovery and WR re-arm after a forced GT recovery.

## 10. Invariants and recurring traps

- There is one supported integrated hardware build.
- There is one TCLK decoder and one ACLK decoder; other paths consume taps.
- `clk_40m` runs at 200 MHz in the integrated design.
- Register offsets always use a 16-byte stride.
- POP must be exactly one AXI store per event.
- Read EVENT/DATA/timestamp before POP, and never use two consumers per FIFO.
- Timestamp zero means UNSYNC and must not enter normal published data.
- All three WR copies must lock before a normal capture.
- Any WR dropout or ACLK-domain reset requires a new absolute-time arm.
- The WR guard and NTP-disciplined Linux clock are part of unattended operation.
- PPS must be wide enough to sample and aligned to the 10 MHz reference.
- Pmod timing signals must be push-pull 3.3 V CMOS; open-drain sources interact
  badly with the carrier's auto-direction translators.
- The SFP laser is enabled by driving active-high TX_DISABLE low.
- The current ACLK path expects a physical fiber TX-to-RX loop.
- SmartConnect/reset topology reflects proven hardware behavior.
- Build success does not imply timing closure; inspect routed WNS.
- Redis stream IDs may be monotonic-adjusted, but `_` preserves the true timestamp.
- Binary Redis `_` fields require consumers not to enable global response decoding.
- The PL drop mask survives process restarts until explicitly cleared or PL reload.
- Redis retention is bounded; long captures require the CSV archiver.
- Design plans explain intent but are not stronger evidence than code and tests.

## 11. First-day maintainer path

1. Read this guide, [PROJECT.md](PROJECT.md), and [OPERATIONS.md](OPERATIONS.md).
2. Read the header comments in `aclk_pipeline_bd_top.v`, `wr_timebase.sv`,
   `aclk_readout_axi.sv`, `readout_common.py`, and `redis_sink.py`; they preserve
   important bring-up history.
3. Run the Python tests and the `aclk_pipeline_chain` cocotb test.
4. Read `vivado/build_aclk_pipeline.tcl` to see the exact synthesized hierarchy,
   clock configuration, reset topology, and addresses.
5. Trace one event from decoder strobe through FIFO registers and Redis `_` payload.
6. On hardware, identify UIO devices by name, arm WR, and run a short reconciled
   capture before changing performance-sensitive code.
7. Keep external accelerator ACLK interoperability/CRC validation visible as an
   open acceptance item.

## 12. Focused references

- [Repository README](../README.md): entry point and quick start.
- [Project architecture](PROJECT.md): concise architecture and validation status.
- [Operations runbook](OPERATIONS.md): build, load, wiring, capture, and recovery.
- [ACLK-Lite framing](aclk-lite-framing.md): authoritative baseband protocol.
- [Hardware interface guide](generated/tclk-aclk-pipeline-hardware-interface-guide.md):
  complete ports, fields, registers, and recovery table.
- [Deployment README](../deploy/README.md): artifacts, overlay, and UIO loading.
- [White Rabbit runbook](../deploy/wr.md): focused sync and diagnostics.
- [RedisAdapter publishing](../deploy/redis.md): exact Redis keys and payload contract.
- [Vivado README](../vivado/README.md): build and GT IP notes.
- [Constraints README](../constraints/README.md): physical constraint caveats.

