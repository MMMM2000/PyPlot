# Offline replay of the real filter and current-hold subsystem

This is an **offline test tool**, not a new controller and not approval to run
hardware. Production controller code and parameters are unchanged.

## What actually executes

`data_logging/mini_dma_logger/current_hold_replay.py` binds an explicit allowlist
of production `MainWindow` methods onto a plain Python host. The bound functions
share the original compiled code and defaults; their globals differ only in an
instance-local virtual clock. This avoids both copying the algorithm and
monkeypatching the application clock process-wide. Tests check code identity.

The host runs the real scale buffer, robust trend/noise filter, Prague and Košice
hold entry/resume methods, long-window cycle-center classification and resume,
feedback freshness checks, and post-hold schedule adjustment. Unknown method
dependencies raise errors rather than silently becoming mocks. No window,
QApplication, QSettings, worker, serial connection, USB controller, or supply
broker is constructed. Constructor tripwires protect that boundary in tests.

Synthetic inputs exercise a fake current supply and fake motor completion times.
The supply's linear ramp is a small adapter, not the full production recipe
runner; it uses the real hold-time compensation and post-hold throttle. It does
not reproduce PSU quantization, electrical dynamics, wire-break logic, voltage
limits, endpoint recovery, or the complete stale-feedback watchdog. Scripted
stress inputs are not a validated physical wire model.

## Recorded-input replay

Run from the repository root using **disposable copies** of recording sidecars:

```powershell
uv run python scripts/mini_dma_current_hold_replay.py COPIED_RUN artifacts/hold-replay-new
```

The input folder must contain `metadata.json`, `scale_raw.csv`,
`control_trace.csv`, and `measurement.csv`. The output directory must not exist.
The default window is the first 180 seconds of recorded current/hold activity;
`--duration-s` changes it. Only the initial upward leg is supported, with a stop
at the first causally observed current reversal. Setup and target-ramp phases
are not replayed. Target changes reset hold state; the first 20 seconds of each
step are flagged as cold-start. Recorded operator-bypass flags are honored.

The output contains per-tick CSVs and a JSON report with settings, source-file
and code hashes, runtime-override audit, clock diagnostics and explicit limits.
Target-only runtime changes are accepted because each trace row supplies its
target. Other runtime changes and unhandled calibration reports fail closed.
Recorded calibrated `applied_load_g` is the input: historical tare/sign setup
and unknown calibration noise are not reconstructed.

### Clock alignment

Do not join these recordings by `elapsed_s`: setup resets the measurement/control
clock while the raw-scale clock continues. Both inspected runs contain one
control-clock reset. Whole-second control UTC also cannot identify a unique raw
sample at the decision instant.

Three UTC-bin scenarios use the early, midpoint and late positions in each
control event's second. A raw sample is available only after the upper end of
its millisecond timestamp bin. Measurement setpoints are joined only after the
entire measurement timestamp bin. There is no nearest-neighbor join, future
interpolation, or fitted lag. `--assumed-log-delay-s` subtracts a declared delay
for sensitivity checks; it is not an estimated logging latency. These scenarios
are **not guaranteed bounds** on the true decision time.

When the sidecars have contiguous sample indices, control sample counts and
latest-sample ages, a fourth **sample-indexed** scenario selects the recorded
raw prefix and reconstructs the observation time from latest-sample UTC plus
age. It checks UTC-bin consistency and monotonicity. Independent millisecond
rounding can cause tiny overlapping arrival intervals: overlaps up to 2 ms
delay the arrival to the preceding tick and are counted; larger overlaps fail.
Counter and age fields are not an atomic decision snapshot, so this improves
alignment without claiming exact historical execution.

Historical trace rows are irregular observation ticks, not a complete timer
stream. Recorded motor completion/settling is not fully reconstructed: the
long-window resume's motor-idle gate is conservatively vetoed in recorded
replay. The actual gate is separately exercised with known fake-motor state.
Historical supply setpoints are observations only. Replay hold decisions do
not change recorded loads: this is **shadow replay, not closed-loop validation**.

## Initial evidence (2026-10-26 UTC)

First three minutes of copied `test_run32` and `iso-stress_run02` recordings:

| Recording / alignment | Eligible ticks | Warm hold-state agreement | Filter noise MAE | Filter slope MAE |
| --- | ---: | ---: | ---: | ---: |
| run02, sample-indexed | 930 (829 warm) | 99.03% | 0.00360 MPa | 0.00658 MPa/s |
| run02, whole-second UTC scenarios | 930 (832 warm) | 58.89–67.31% | 0.102–0.181 MPa | 0.368–0.740 MPa/s |
| run32, whole-second UTC scenarios | 1208 (1115 warm) | 83.41–83.95% | not recorded | not recorded |

The indexed run02 clock-origin spread is 2.04 ms. Four overlapping arrival
intervals required a rounding adjustment, at most 0.421 ms. No consumed sample
was later than its replay tick. Its only runtime override changed target fields,
not hold settings. The sample-indexed scenario produced 16 pauses and resumes;
whole-second scenarios produced only 3–4, demonstrating why coarse alignment
is inadequate for validating transition counts. Historical agreement is a
descriptive comparison, **not an accuracy score or a safety certificate**.

These results support the alignment and subsystem adapter, not further
controller tuning. Exact historical equivalence would additionally need atomic
decision/sample IDs, full control-tick logs, calibration/recipe state and motor
completion evidence. Real-wire validation remains a separate, explicitly
authorized task with normal hardware safety checks.
