# Mini DMA Wire Simulator

`data_logging.mini_dma_logger.wire_simulator` provides a deterministic virtual wire and processed-center control harness for software-only Mini DMA controller experiments. It does not import Qt, serial, Tic, or power-supply code.

The model treats motor displacement and transformation contraction as the two contributors to tensile extension. Stress is computed from an elastic stiffness in MPa/mm, then optional direct stress fluctuations, noise, spikes, and drift are added. Raw stress samples are preserved for safety rails. Motor decisions use the processed control signal: median center, MAD noise, slope, sample count, freshness, and the raw envelope.

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

`data_logging.mini_dma_logger.full_run_simulator` builds on the same virtual wire model to exercise a complete first-overheating style sequence: target acquisition, current rise, current endpoint recovery, optional reverse/current unwind, bounded mechanical corrections, delayed scale feedback, and slack take-up. It is still software-only and deterministic. It does not open Qt, serial ports, Tic drivers, power supplies, or the Mini DMA logger hardware path. The full-run model treats the wire's current/history-driven free transformation strain as hidden material state; stress is derived from the mismatch between that hidden contraction/elongation and the controller-produced motor strain, then delayed/noisy scale feedback is fed back to the controller.

Run the full scenario matrix:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --out artifacts/mini-dma-full-run-sim
```

Run one full-run scenario:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --scenario transformation_recovery --out artifacts/mini-dma-full-run-sim/transformation_recovery
```

Run the calibrated realistic first-overheating scenario:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --scenario realistic_first_overheating --out artifacts/mini-dma-realistic-full-run
```

Run the bad Co6-style first-overheating scenario:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --scenario bad_co6_first_overheating --out artifacts/mini-dma-bad-co6-full-run
```

Run the parameter sweep across wire diameter, stiffness, and noise:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --sweep --out artifacts/mini-dma-full-run-sim/parameter-sweep
```

Run the broader free-strain stress-test matrix:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --free-strain-matrix --out artifacts/mini-dma-free-strain-stress-matrix
```

Run representative correction-policy comparisons:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --policy-matrix --out artifacts/mini-dma-control-policy-matrix
```

Run response-gated adaptive correction-cap comparisons:

```powershell
uv run python scripts/mini_dma_full_run_simulator.py --adaptive-policy-matrix --out artifacts/mini-dma-adaptive-policy-matrix
```

Full-run scenarios currently cover:

- `baseline_first_overheating`: nominal endpoint recovery.
- `realistic_first_overheating`: calibrated software-only 50 MPa good-wire run, based on completed `Ni50Fe27Ga23 12/2` 1-80-1 mA current-sweep stress runs with about 10% strain span; it starts unloaded, ramps the simulated target from 0 MPa to 50 MPa, then runs the current rise, current holds, endpoint recovery, and reverse unwind with 200 ms delayed scale feedback. The stress disturbance is simulated from current-driven transformation progress, while the strain-current curve is calculated only from simulated motor position and gauge-length conversion. The correction cap is expressed as strain percent of gauge length so it scales with the wire. The calibration is anchored to real run34 hold behavior, where several near-fixed-current holds move strain by more than 1% and the largest hold spans about 4% strain.
- `bad_co6_first_overheating`: bad `Ni47Fe24Ga23Co6 2/1`-style 50 MPa run based on the stiff-validation failure. It starts unloaded, ramps the target from 0 MPa to 50 MPa, then applies the real run length and diameter, a very early transformation stress surge near 10 mA, low usable strain, 200 ms delayed feedback, and a raw stress break rail so controller experiments can distinguish recoverable tuning problems from nonrecoverable bad-wire response.
- `low_strain_noisy_first_overheating`: low-strain 50 MPa wire where the hidden free transformation strain is small and stress noise/fluctuation is comparatively large; this guards against controller policies that manufacture a large measured strain-current loop from a weak material response.
- `noisy_centered_first_overheating`: high raw noise centered near target, expected to complete without unnecessary chasing.
- `transformation_recovery`: current rise contraction forces current-hold recovery before endpoint completion.
- `reverse_unwind_recovery`: reverse/current unwind must recover the processed center before completing.
- `slack_after_unwind_takeup`: near-zero-load slack recovery keeps taking up tension until the processed center recovers.
- `thin_wire_delayed_feedback`: 8.3 um wire and low sample cadence remain bounded.
- `stress_ladder_50_100_after_unwind`: good-wire stress ladder that ramps 0 -> 50 MPa, runs a full current cycle, applies a post-unwind free-length elongation/slack disturbance, then ramps 50 -> 100 MPa before another full current cycle. This is the software-only regression for the failure mode where post-unwind slack or bad apparent stiffness can make the next stress ramp unsafe or too slow. The second target ramp uses a target-scaled feedback lead gate and waits for fresh post-ramp scale feedback so the requested target cannot sprint far ahead of a slack/relaxed wire. Current sweep advancement uses the tight processed tolerance, not the broader endpoint recovery band, so simulated current holds keep correcting while the processed center is still clearly biased.

Each full-run output folder contains `measurement.csv`, `control_trace.csv`, `summary.json`, `config.json`, `report.md`, and `full_run.png`. The measurement CSV logs raw and processed stress, stress target/error, current setpoint, measured current, simulated voltage/resistance/power, motor position, measured motor strain, hidden free transformation contraction/strain, elastic mismatch strain, free-strain tracking error, phase, current-hold state, correction, cumulative correction travel, and feedback age for each simulated sample. The plot shows stress versus time with the active target trajectory and hold bands, measured strain versus current with a hidden free-strain reference, current/motor versus time, and controller correction decisions. The simulator advances a synthetic clock, so a run can report about 15-40 minutes of bench-equivalent measurement time while executing in seconds, depending on whether one or multiple target stresses are simulated. The default full-run harness applies `scale_latency_s = 0.2`, so controller decisions use delayed feedback rather than the just-created physical sample. The `summary.json` records total measurement time, current-hold time and periods, maximum stress error, target-ramp-specific stress error including later target ramps, recovery times, measured strain range, hidden free-transformation strain range, measured-vs-free strain tracking error, correction travel, configured strain-percent correction cap, effective mm correction cap, target stress sequence, inter-target free-length shift, and invariants such as no load/stress cruise, bounded single-correction size, no accumulated correction-travel stop, endpoint waits only while unrecovered, endpoint completion only after recovery, and delayed-feedback application.

The free-strain stress-test matrix crosses real-run-inspired wire families with controller-relevant perturbations:

- good `Ni50Fe27Ga23 12/2`-style wires with about 10% hidden transformation strain,
- early `19/8`-style wires with about 9% hidden transformation strain and lower stiffness,
- bad `Co6 2/1`-style wires with about 1% usable transformation strain,
- weak/noisy wires with about 0.25% transformation strain,
- nominal, fast/spiky, rough-transform, delayed-feedback, soft-underestimated, and stiff-overresponsive variants.

`free_strain_fluctuation_pct` is a physical hidden-length perturbation during transformation. It changes the simulated free contraction/elongation and therefore changes stress through the elastic mismatch; it does not directly fabricate the plotted strain. Plotted strain remains derived from simulated motor position and gauge length. The sweep writer emits JSON, CSV, Markdown, and a compact metrics PNG so policy changes can be compared across all simulated wire families.

The policy matrix reuses representative high-strain, delayed-feedback, stiff-overresponsive, weak/noisy, and 50 -> 100 MPa post-unwind stress-ladder cases and varies only geometry-based correction-cap scale plus the recovery band as a fraction of target stress. It is intended to catch policies that improve the good 10% strain wire by moving faster but overdrive weak/noisy wires into artificial strain, long current holds, or poor later target-ramp recovery. Current results show good high-strain ladder cases benefiting from larger percent caps around 0.30-0.42% per command, while weak/noisy wires prefer much smaller caps around 0.05%; that is evidence for adaptive cap growth rather than one universal cap.

The adaptive policy matrix is deliberately off by default in ordinary scenarios. It compares response-gated cap ceilings that can grow only during current-hold correction, based on processed-center error, processed noise, and whether same-sign corrections are actually improving the processed error. It does not use hidden free-strain truth. Current software-only results are mixed: delayed-feedback early wires can benefit from a larger temporary cap, weak/noisy wires only tolerate small growth, and the good 50 -> 100 MPa stress-ladder cases still prefer the fixed geometry-percent cap. Treat this as evidence for future controller design, not as a live-control default.
