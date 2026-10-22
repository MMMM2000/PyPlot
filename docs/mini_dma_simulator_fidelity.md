# Offline simulator fidelity assessment — 2026-10-22 UTC

## Scope and source

This increment is based on integration commit
`2679af19e12d04cf21b56bf51014e8c4e9c55211`, not the public PR #301
head `b556eaac`. That integration branch contains 88 additional commits.
Primary-checkout and production policy files are unchanged. No device access,
bench process, campaign, deployment or hardware acceptance is involved.

The speed simulator is a parallel hold/resume **policy-shape model**. Its
`baseline` label does not mean the current production controller. The actual
pure `ForceControlPolicy` is exercised by `force_control_simulator.py`;
that harness does not include the complete current-sweep state machine.
There is consequently no validated end-to-end production-versus-candidate
comparison in this report.

## Fidelity map

| Area | Production / evidence | Simulator coverage and remaining gap |
| --- | --- | --- |
| Control | Production uses force intents, freshness, pending motion and response observation; current hold has additional integration logic | Pure-policy harness exercises those policy inputs; speed model does not import the production policy |
| Scale filtering | Production buffer, robust outlier rejection, pairwise slopes, optional endpoint estimate and arrival timestamps | Speed model uses its own median/window logic, correlated noise and synthetic gaps; no filter-equivalence claim |
| Current timing | Separate setpoint, measured current and supply reads; recorded schedules differ by run | Speed model advances ideal current on its sample clock; no PSU settling/compliance model |
| Motor | Recorded resolution 800 steps/mm; configured backlash is not measured backlash | Speed model has bounded moves and optional first-order lag, but no real command queue/backlash/contact model |
| Transformation | Heating/cooling and specimen history can matter | Current-threshold hysteresis with optional first-order fraction lag, not a thermal energy model |
| Calibration | Specimen dimensions and force-to-stress conversion recorded | Explicit assumed controller stiffness is separate from hidden plant stiffness; neither is newly calibrated |
| Faults | Terminal run includes loss-of-load regime; production has multiple protections | Speed simulation retains an oracle true-stress abort; pure policy receives synthetic missing feedback; no device-fault acceptance |
| Reproducibility | Historical outputs retained separately | Version 2 records source SHA-256, complete scenario/policy configs and seeds |

Measurement delay is a transport/zero-order-hold sensitivity: before the first
delivery it holds the first observation. It does not reproduce the production
freshness watchdog. Quantization rounds the noisy measurement to a specified
MPa quantum. Transformation lag evolves the equilibrium fraction by an
exponential first-order response. Defaults disable all three and retain the
historical controller stiffness assumption of 600 MPa/mm.

These are explicit bounded sensitivities, not fitted bench parameters. The
oracle safety stop, single time grid, sample-weighted error percentiles and
ideal current actuator remain limitations. The reported elapsed/hold times,
motor travel/reversals, reholds, true error percentiles and abort/completion
counts must be assessed together; unfinished runs cannot be credited as faster.

## Reproducible baseline and sensitivity result

The original 192 summary records reproduced exactly. All 288 default summary
records (six scenarios, four policies, seeds 0–11) are identical before/after
this change. The two extra default scenarios are sparse feedback and heavy tail.

The historical combined candidate reduced median elapsed time from 3796.75 to
1329.75 seconds (64.98%) **only in stationary hunting**. Median true-error P95
fell from 12.4997 to 12.0404 MPa. It was unchanged in the other five scenarios.

Five additional sensitivities used four scenarios, two policies and 12 matched
seeds each (480 runs): delay 0.5 s; transformation tau 5 s; plant stiffness
300 or 1200 MPa/mm with controller assumption fixed at 600; and a combined
case (0.5 s, 5 s, 300 MPa/mm, measurement quantum 0.342266 MPa).
The quantum approximates 0.01 g at the recorded diameter; this is a
sensitivity scale, not a verified sensor calibration.

Stationary-hunting paired median elapsed differences remained negative:
-2100.25 s combined, -2469.25 s delay, -3040.125 s double gain,
-2161.25 s half gain, -2509 s thermal lag. All pairs completed without oracle
safety stops; other tested regimes were unchanged. The default paired median
was -2455.625 s, with a 95% paired-seed bootstrap interval
[-2730.5, -2195.875] s (10,000 resamples, RNG seed 20261022).
This interval captures synthetic seed variation only, not model uncertainty
or uncertainty about hardware benefit. No controller retuning was performed.

## Copied-run evidence and held-out diagnostic

Two completed historical run directories were copied read-only with matching
SHA-256 hashes. Full paths, sample identifiers and file hashes remain in ignored
local evidence, not this public document. The earlier run supplies the first
60% of measurements for fitting; its final 40% and the entire later run are
held out. No held-out coefficients are fitted.

Recorded initial lengths differ: 58.328 and 38.77 mm. Both record a diameter of
0.0191 mm. The field's identity as the metal core has not been independently
verified; prior specimen heat history is unknown. The later recipe includes
a first-overheating stage, which is not a complete heat history.

Actual measurement interval median/P95: 0.8130/1.3204 s and 0.6075/0.6736 s.
Raw-scale intervals: 0.2027/0.4068 s and 0.2025/0.2027 s. These channels have
different elapsed-time origins and are not silently aligned. Recorded stress
agrees with the recorded load/diameter conversion to within 0.000017 MPa.
That checks arithmetic consistency, not diameter accuracy or force calibration.

The audit fits only a deliberately simple incremental linear load predictor
using recorded displacement, current-squared change and elapsed interval.
It is a falsification baseline, not a replacement plant model. It reports:

| Split | One-step RMSE (g) | Persistence RMSE (g) | Recorded-input rollout RMSE (g) |
| --- | ---: | ---: | ---: |
| Training | 0.1788 | 0.1827 | 1.5051 |
| Held-out segment | 0.2776 | 0.2787 | 2.2747 |
| Held-out run | 0.1207 | 0.1227 | 69.3677 |

One-step predictions condition on the last measured load; rollouts use only
one initial load and then accumulate predictions. The large rollout drift,
despite small one-step gains over persistence, rejects this simple model as a
predictive plant. This is **not** a measured-versus-closed-loop validation of
the existing speed simulator. Closed-loop records confound actuator response,
transformation, target changes and drift even when the design matrix has full
rank. Keep the final fault regime in the report; do not select it away after
seeing the result.

## Reproduction

Use the required Python 3.14 environment via uv and isolated artifacts paths.

```powershell
uv run python -m data_logging.mini_dma_logger.iso_stress_speed_simulator --policy baseline --policy adaptive_response_window --policy processed_crossing --policy adaptive_response_crossing --seeds 12 --out artifacts/fidelity/defaults
uv run python -m data_logging.mini_dma_logger.iso_stress_speed_simulator --scenario prague_stationary_hunting --policy baseline --policy adaptive_response_crossing --seeds 12 --measurement-delay-s 0.5 --transformation-tau-s 5 --plant-stiffness-mpa-per-mm 300 --estimated-stiffness-mpa-per-mm 600 --measurement-quantum-mpa 0.342266 --out artifacts/fidelity/combined
uv run python scripts/mini_dma_simulator_trace_audit.py --train-run artifacts/copied-run-a --heldout-run artifacts/copied-run-b --out artifacts/fidelity/audit
uv run python -m pytest tests/test_mini_dma_simulator_fidelity.py tests/test_mini_dma_iso_stress_speed_simulator.py tests/test_mini_dma_force_control_simulator.py
```

The trace audit requires copied metadata.json, measurement.csv and scale_raw.csv;
it hashes additional CSV/JSON sidecars, refuses a nonempty output directory and
refuses output within either input directory. It emits report.json, per-split
prediction CSVs and heldout_residuals.png. Never point testing at persistent
project/app-data stores. Keep each run's artifacts; do not overwrite old evidence.

## Next validity gate

Before any production comparison, replay actual filtering and current-hold
decisions through fake drivers on aligned timestamps, distinguish commanded
from achieved position, and identify response only in uncontaminated motion
windows. Hold out complete thermal regimes and specimens. Establish dimensions,
history, latency and compliance from independent evidence or leave them bounded
unknowns. Add saturation, backlash, contact-loss and fault scenarios only with
explicit assumptions. A simulation pass is not hardware acceptance.
