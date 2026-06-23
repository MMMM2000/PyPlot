# Mini DMA Wire Simulator

`data_logging.mini_dma_logger.wire_simulator` provides a deterministic virtual wire and processed-center control harness for software-only Mini DMA controller experiments. It does not import Qt, serial, Tic, or power-supply code.

The model treats motor displacement and transformation contraction as the two contributors to tensile extension. Stress is computed from an elastic stiffness in MPa/mm, then optional transformation fluctuations, noise, spikes, and drift are added. Raw stress samples are preserved for safety rails. Motor decisions use the processed control signal: median center, MAD noise, slope, sample count, freshness, and the raw envelope.

Built-in scenarios cover the current control questions:

- `low_noise_centered`: low-noise processed stress center already near target, expected `no_move`.
- `high_raw_centered`: high raw fluctuations centered near target, expected `no_move`.
- `high_raw_far_above`: high raw fluctuations centered far above target, expected `bias_recovery`.
- `transformation_current_rise`: current rise contraction biases stress above target, expected `bias_recovery`.
- `reverse_current_unwind`: reverse current path leaves stress away from target, expected `bias_recovery`.
- `slack_after_unwind`: slack/no-contact style low stress after unwind, expected bounded `bias_recovery`.
- `bad_low_apparent_stiffness`: low apparent stiffness estimate stays bounded by the correction cap, expected `bias_recovery`.
- `thin_wire_tiny_load`: 8.3 um wire with tiny target load, expected `no_move`.
- `thick_wire_larger_load`: thicker/stiffer wire with larger target load, expected `no_move`.
- `delayed_scale_feedback`: low sample cadence, expected bounded `bias_recovery`.
- `high_bias_cloud`: 60-70 MPa cloud against a 20 MPa target, expected `bias_recovery`.
- `wide_high_cloud`: 10-300 MPa transformation-sized cloud, expected raw `safety_stop` when rails are exceeded.
- `target_spanning_cloud`: noisy cloud centered near 20 MPa, expected `no_move`.
- `transformation_bias`: same-sign biased cloud caused by contraction, expected `bias_recovery`.
- `sign_crossing_reversal`: robust center crosses after a previous error sign, expected `wait_reversal`.
- `wire_break`: stress crosses the virtual break rail, expected `safety_stop`.

Run all scenarios:

```powershell
uv run python scripts/mini_dma_wire_simulator.py --out artifacts/mini-dma-wire-sim
```

Run all scenarios with a Markdown summary and PNG report:

```powershell
uv run python scripts/mini_dma_wire_simulator.py --out artifacts/mini-dma-processed-center-control-sims --report
```

Run one scenario:

```powershell
uv run python scripts/mini_dma_wire_simulator.py --scenario target_spanning_cloud --out artifacts/mini-dma-wire-sim/target-spanning
```

Each output folder contains:

- `measurement.csv`: synthetic scale/current/motor samples at about 4-5 Hz.
- `control_trace.csv`: controller-like robust-center decisions in a Mini DMA trace-compatible shape.
- `summary.json`: final decision, raw stress range, stop reason, and expected decision.
- `scenario.json`: full simulator parameters for reproducibility.
- `scenario_matrix_summary.json`, `scenario_matrix_report.md`, and `scenario_matrix.png` when `--report` is used.

Controller work can consume the CSV files or import `run_virtual_wire_scenario()` and compare its `MeasurementSample` stream against Mini DMA processed-center decisions. The simulator is deliberately simple: it is a repeatable control-test fixture, not a calibrated thermomechanical material model.

## Full-run software validation

`data_logging.mini_dma_logger.full_run_simulator` builds on the same virtual wire model to exercise a complete first-overheating style sequence: target acquisition, current rise, current endpoint recovery, optional reverse/current unwind, bounded mechanical corrections, delayed scale feedback, and slack take-up. It is still software-only and deterministic. It does not open Qt, serial ports, Tic drivers, power supplies, or the Mini DMA logger hardware path.

Run the full scenario matrix:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --out artifacts/mini-dma-full-run-sim
```

Run one full-run scenario:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --scenario transformation_recovery --out artifacts/mini-dma-full-run-sim/transformation_recovery
```

Run the parameter sweep across wire diameter, stiffness, and noise:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --sweep --out artifacts/mini-dma-full-run-sim/parameter-sweep
```

Full-run scenarios currently cover:

- `baseline_first_overheating`: nominal endpoint recovery.
- `noisy_centered_first_overheating`: high raw noise centered near target, expected to complete without unnecessary chasing.
- `transformation_recovery`: current rise contraction forces current-hold recovery before endpoint completion.
- `reverse_unwind_recovery`: reverse/current unwind must recover the processed center before completing.
- `slack_after_unwind_takeup`: near-zero-load slack recovery keeps taking up tension until the processed center recovers.
- `thin_wire_delayed_feedback`: 8.3 um wire and low sample cadence remain bounded.

Each full-run output folder contains `measurement.csv`, `control_trace.csv`, `summary.json`, `config.json`, `report.md`, and `full_run.png`. The plot shows processed stress center versus target, the raw stress envelope, current, motor displacement, and controller decisions. The default full-run harness applies `scale_latency_s = 0.2`, so controller decisions use delayed feedback rather than the just-created physical sample. The `summary.json` records invariants such as no load/stress cruise, bounded single-correction size, no accumulated correction-travel stop, endpoint waits only while unrecovered, endpoint completion only after recovery, and delayed-feedback application.
