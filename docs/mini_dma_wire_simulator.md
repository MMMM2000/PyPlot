# Mini DMA Wire Simulator

`data_logging.mini_dma_logger.wire_simulator` provides a deterministic virtual wire and robust-center control harness for software-only Mini DMA controller experiments. It does not import Qt, serial, Tic, or power-supply code.

The model treats motor displacement and transformation contraction as the two contributors to tensile extension. Stress is computed from an elastic stiffness in MPa/mm, then optional transformation fluctuations, noise, spikes, and drift are added. Raw stress samples are preserved for safety rails, while controller-like decisions use a recent median and median-absolute-deviation noise estimate.

Built-in scenarios cover the current control questions:

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

Run one scenario:

```powershell
uv run python scripts/mini_dma_wire_simulator.py --scenario target_spanning_cloud --out artifacts/mini-dma-wire-sim/target-spanning
```

Each output folder contains:

- `measurement.csv`: synthetic scale/current/motor samples at about 4-5 Hz.
- `control_trace.csv`: controller-like robust-center decisions in a Mini DMA trace-compatible shape.
- `summary.json`: final decision, raw stress range, stop reason, and expected decision.
- `scenario.json`: full simulator parameters for reproducibility.

Future controller work can either consume the CSV files or import `run_virtual_wire_scenario()` and compare its `MeasurementSample` stream against the current Mini DMA controller and proposed bias-control logic. The simulator is deliberately simple: it is a repeatable control-test fixture, not a calibrated thermomechanical material model.
