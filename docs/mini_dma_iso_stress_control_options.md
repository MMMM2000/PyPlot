# TMA iso-stress control options

Status: offline design catalogue. This document does not authorize controller
or hardware changes.

Date: 2026-07-29

## Problem to solve

The current iso-stress sweep can spend most of its wall time at a fixed current
while processed stress repeatedly crosses the target. The controller reacts to
the phase of that fluctuation with alternating motor commands. Those commands
change the plant, delay the next trustworthy response, and repeatedly reset the
evidence needed to resume current.

The difficult part is that a useful transforming wire can produce the same
short-window signature: large stress variation, motor reversals, and a
temporarily centered distribution. A safe solution must therefore distinguish:

1. zero-mean fluctuation that should not be chased;
2. delayed response to the last motor command;
3. coherent thermal or transformation drift that should be followed;
4. real stress error requiring correction;
5. unsafe raw excursions that must bypass all processing.

Run 15 showed that fixing resume eligibility alone is insufficient. The
offline processed-observation trigger helped a synthetic hunting model but also
triggered widely in the finalized transforming Prague trace, so it is not safe
enough for live use.

## Evaluation criteria

Every approach should be judged on:

- stress RMS, p95, p99, maximum, and time outside the pause/safety bands;
- hold fraction, longest hold, and full up/down sweep time;
- motor commands, travel, reversals, and largest correction;
- re-hold frequency after resuming current;
- transformation onset/offset, peak strain, loop area, and endpoint coverage;
- behavior under calm noise, oscillatory noise, coherent transformation,
  delayed response, sparse feedback, outliers, and sensor failure;
- complexity, observability, tunability, and ease of rollback.

## Ranked approach summary

| Approach | Likely value | Risk to transforming samples | Complexity | Recommendation |
|---|---:|---:|---:|---|
| Robust mean/drift state estimator | Very high | Low if safety remains separate | Medium | Core next step |
| One outstanding motor response | Very high | Low | Low-medium | Core next step |
| Disturbance/transform drift observer | Very high | Low-medium | Medium-high | Core next step |
| Event-triggered motor control | High | Low | Medium | Combine with estimator |
| Adaptive current-slew supervisor | High | Low | Medium | Add after force loop works |
| Gain scheduling from identified response | High | Medium | Medium | Add conservatively |
| Reversal penalty and anti-chatter logic | Medium-high | Low | Low | Useful supporting layer |
| Dual-timescale processing | Medium-high | Low | Low-medium | Estimator input, not actuator |
| Statistical equivalence resume | Medium | Medium | Medium | Supervisor only |
| Change-point detection | Medium | Low-medium | Medium | Transformation/drift veto |
| Resistance/strain/temperature sensor fusion | High | Low | Medium-high | Strong future discriminator |
| Iterative learning across repeated sweeps | Medium-high | Medium | Medium-high | Later, after stable base loop |
| Step-and-settle current recipe | Medium | Low | Low | Diagnostic/fallback mode |
| Fixed hold time limit | Low-medium | High | Low | Diagnostic only |
| Longer median alone | Low | Medium-high | Low | Do not use alone |
| Wider stress/resume bands | Medium speed gain | High | Low | Reject as general fix |
| Lower motor gain alone | Low-medium | Low | Low | May reduce violence, not cause |
| Fixed correction cooldown | Medium | Medium | Low | Infer delay instead |
| End-to-end machine learning control | Unknown | High | Very high | Do not pursue now |
| Mechanical/sensor improvements | Potentially high | Low | Hardware-dependent | Investigate in parallel |

## Software control approaches

### 1. Robust mean and drift estimator

Estimate the latent mean stress rather than controlling every short-window
fluctuation. A practical initial state is:

```text
state = [
    mean_stress,
    mean_stress_drift,
    motor_to_stress_gain,
    disturbance_drift,
]
```

Update it only for fresh scale samples using robust residual weighting. Estimate
effective sample count from autocorrelation and real cadence; twenty correlated
samples must not be treated as twenty independent observations.

Possible implementations, in increasing complexity:

- robust exponentially weighted mean plus alpha-beta drift estimator;
- Huber-loss Kalman filter;
- adaptive state-space/Kalman filter with gain uncertainty;
- Bayesian or particle filter for strongly non-Gaussian transformation.

Recommendation: begin with the robust alpha-beta or Huber-Kalman form. It is
inspectable, deterministic, and easier to validate than a particle filter.

### 2. One outstanding motor response

Do not issue another correction until the previous command has:

1. been accepted by the Tic;
2. physically completed;
3. passed a settle/dead-time estimate;
4. accumulated enough fresh post-move samples to estimate its signed effect.

This is stronger than a fixed delay. The response interval should be learned
from actual command duration, scale cadence, and measured post-command
response. If the response is ambiguous, the controller waits or uses a
one-step-safe fallback; it does not compound another large move.

This directly addresses run 15's alternating corrections and should be
implemented even if the final estimator changes.

### 3. Event-triggered control

Run the estimator on every fresh sample but actuate only when the estimated
mean error is statistically and practically significant:

```text
actuate when:
    confidence interval is outside the control deadband
    AND no response is pending
    AND projected error is not naturally returning
```

High raw noise widens uncertainty and therefore suppresses unnecessary action;
it does not enlarge the estimated error. Hard raw-stress safety remains a
separate bypass.

### 4. Disturbance observer

Separate motor-induced response from external/internal disturbance:

```text
observed mean change =
    predicted motor response
  + estimated disturbance drift
  + residual noise
```

Persistent disturbance drift represents transformation or thermal change and
earns bounded following motion. Alternating residuals with near-zero mean are
classified as fluctuation and do not earn alternating moves.

An extended-state observer, two-state Kalman model, or conservative recursive
least-squares disturbance estimate are all plausible. Start with the simplest
model that can reproduce delayed response and coherent drift.

### 5. Conservative PI control on estimated mean

Use proportional-integral control only on the latent mean, not on raw or
short-window stress:

- proportional action is bounded by response uncertainty;
- integral action is frozen while a response is pending, data is stale, or
  uncertainty is high;
- anti-windup clamps the accumulated correction;
- sign reversal either cancels or heavily discounts the integral;
- every command remains inside existing travel, stress, and motor limits.

Derivative action on scale data is unlikely to help because it amplifies noise.
If used, derive it from the estimated state rather than sample differences.

### 6. One-step predictive control

Predict stress over the next response horizon for candidate motor moves and
choose the smallest move that brings predicted mean stress into the control
band. This is a small model-predictive controller, not a large optimizer.

Advantages:

- naturally accounts for delay and current slew;
- can penalize motor travel and reversals;
- can enforce stress and position constraints directly.

Risks:

- depends on a credible local response model;
- a wrong transformation model can produce confident but incorrect action.

Recommendation: compare this against conservative PI after the estimator and
response model are validated.

### 7. Smith predictor / dead-time compensation

Model the response delay explicitly and predict current mean stress while
waiting for scale feedback. This can reduce delay-induced hunting, but a
classical fixed Smith predictor assumes a fairly stable plant. Wire stiffness
and transformation response are not stable, so any predictor must carry
uncertainty and fall back safely when residuals grow.

### 8. Adaptive gain scheduling

Estimate motor-to-mean-stress gain from accepted one-command responses and use
different conservative gains for:

- current direction;
- stress target;
- transformation-active versus inactive regions;
- low/high uncertainty;
- recently reversed motor direction.

Never learn gain from overlapping commands or from a window dominated by
coherent disturbance. Clamp learned gains to calibrated physical bounds.

### 9. Reversal penalty and backlash-aware action

Repeated reversals are a strong sign of hunting. Penalize a reversal unless the
estimated mean crosses a wider opposite-action threshold. Account for backlash
separately so a reversal does not look like an ineffective command and provoke
an even larger correction.

This is a useful stabilizer, but it cannot replace state estimation because
real transformation can legitimately require a reversal.

### 10. Dual-timescale and adaptive windows

Maintain:

- a fast robust channel for safety, pause entry, and change detection;
- a slower adaptive channel for mean, drift, and confidence.

Choose the slow horizon using independent sample count, autocorrelation,
largest cadence gap, and change-point evidence. Shrink it during coherent
change; expand it only for stationary uncertainty. Do not directly actuate from
a longer median without response-state awareness.

### 11. Frequency-selective filtering

Possible forms include a low-pass filter, notch filter at the observed
oscillation period, or synchronous estimation of a periodic component.

This is attractive if the disturbance has a stable mechanical or mains-related
frequency. It is risky if transformation has energy in the same band or the
period changes with current. Use spectral analysis diagnostically first; only
subtract a periodic component if frequency, phase, and physical source are
stable across samples and targets.

### 12. Change-point and transformation detection

Detect persistent changes in robust mean, drift, resistance, displacement, or
motor-response residuals. Candidate methods:

- cumulative sum or generalized likelihood ratio;
- Bayesian online change-point detection;
- robust piecewise-linear segmentation;
- simple persistence plus direction-consistency gates.

Use this primarily as a veto: when coherent change is active, do not accept a
stationary-noise resume shortcut.

### 13. Sensor fusion

The strongest route to protecting good samples may be distinguishing
transformation using signals other than scale stress:

- resistance and its slope;
- strain/displacement slope;
- net motor travel versus total motor travel;
- current and current direction;
- wire or nearby temperature;
- IR temperature when reliable.

A real transformation should produce a coherent multi-signal signature,
whereas scale fluctuation alone may not. Begin with transparent rules or a
small state model, not a black-box classifier.

### 14. Statistical equivalence resume

Resume current when a confidence interval for mean stress lies inside an
acceptable control band, rather than requiring every short-window estimate to
remain inside a narrow band.

This matches the scientific use of processed data, but it is a supervisor
decision, not a motor-control fix. It is safe only after:

- pending motor response is resolved;
- coherent transformation/drift is excluded or modeled;
- latest/fast stress passes a veto;
- reduced-rate probation follows the resume.

### 15. Continuous adaptive current slew

Replace binary ramp/hold behavior with bounded rate levels:

```text
0, 0.1, 0.25, 0.5, 0.75, 1.0 times recipe rate
```

Choose the level from projected mean-stress risk and uncertainty. Slow current
during coherent disturbance; continue through centered fluctuation. Never
exceed the recipe rate or catch up later.

This can reduce stop/start chatter, but only after the force loop stops chasing
noise.

### 16. Reduced-rate resume probation

Keep the existing concept: resume at reduced current rate, require fresh
evidence, then graduate. Apply it in both current directions unless real data
supports asymmetry. Probation is a safety layer and causal probe, not the main
speed mechanism.

### 17. Iterative learning between repeated sweeps

Repeated up/down loops permit learning the expected current-dependent
transformation disturbance from earlier loops. Iterative learning control could
provide bounded feed-forward motion or rate scheduling on later loops.

Requirements:

- stable loop alignment and provenance;
- uncertainty bounds and conservative first loop;
- no transfer between samples without explicit validation;
- feedback remains authoritative.

This may be valuable later, but a stable feedback controller must come first.

### 18. Data-driven or machine-learning models

Options include Gaussian-process response models, recurrent networks, learned
classifiers, or reinforcement learning. They could model nonlinear
transformation, but current real-run coverage is too small and failure cost is
too high. A learned model may eventually assist prediction or classification,
but should not directly own safety or actuation.

## Recipe and experimental approaches

### 19. Step-and-settle current sweep

Advance current in small discrete steps, settle/estimate mean stress, then
continue. This is slower in ideal conditions but simpler to identify and safer
for controller development. It is a good diagnostic recipe and fallback mode,
not necessarily the final production recipe.

### 20. Short system-identification preamble

At safe current and stress, issue a few bounded single-direction motor probes
to estimate gain, response delay, noise, and backlash before the sweep. Abort
or choose conservative defaults if identification is poor.

Avoid continuous dithering during the scientific sweep because it can
contaminate strain data.

### 21. Current-rate ladder

Use a guarded sequence of increasing current rates on a disposable or stable
sample to map stress error versus rate. This identifies whether the nominal
0.4 mA/s rate is itself too aggressive after the force loop is stabilized.

### 22. Hold-time budget with explicit outcome

After a maximum hold duration:

- continue only if estimated mean and projected risk are acceptable;
- otherwise stop or mark the point invalid;
- never silently treat timeout as successful stress control.

This prevents endless runs but does not fix control. Use it as a campaign guard
and diagnostic.

### 23. Sample/run quality classification

Classify low-transform, high-noise, contact-loss, compliance-limited, and good
transforming runs after measurement. This can guide offline analysis and
campaign decisions. Avoid automatically relaxing live control based on a
premature sample label.

### 24. Conditioning or first-overheating strategy

Some wires may stabilize after a conditioning cycle. That can reduce
run-to-run drift but changes sample history and scientific interpretation. It
must remain an explicit recipe choice, not an invisible controller remedy.

## Hardware and measurement approaches

### 25. Mechanical damping and vibration isolation

Investigate whether oscillation comes from the balance, mounting frame, wire,
airflow, table vibration, or motor mechanics. Potential improvements:

- isolate the balance and frame;
- reduce airflow and acoustic excitation;
- increase structural stiffness or add damping outside the sample path;
- remove cable forces and stick-slip;
- verify motor/backlash mechanics.

Use independent accelerometer or stationary-load recordings to identify the
source before modifying the rig.

### 26. Faster or lower-noise force sensor

A faster balance/load cell reduces dead time and improves independent sample
count. A load cell closer to the specimen could avoid some mechanical modes.
Any sensor change requires new calibration, overload protection, and
cross-validation against the current scale.

### 27. Analog or digital sensor filtering

Sensor-side anti-alias filtering can prevent high-frequency vibration from
folding into the control band. It must have known phase delay; an undocumented
filter can worsen closed-loop stability.

### 28. Temperature measurement and compensation

Measure wire or local temperature with enough bandwidth to separate thermal
expansion from transformation. Use temperature as a disturbance input or
validation signal, not to subtract stress blindly.

### 29. PSU ripple and electromagnetic interference

Check current/voltage ripple, grounding, shielding, serial/USB coupling, and
load-cell interference as a function of current. If stress oscillation is
synchronous with electrical ripple, hardware correction is preferable to
controller compensation.

### 30. Better displacement or motor feedback

An independent encoder or displacement gauge can distinguish commanded motor
position from actual stage/sample motion, expose missed steps and backlash, and
improve response identification. Tic position alone is commanded open-loop
position and is not sufficient evidence of physical motion.

## Approaches that should not be the primary fix

- Simply use a longer median.
- Widen the stress band until the controller stops reacting.
- Increase the 300 MPa guard to tolerate control instability.
- Disable holds or force current to continue.
- Reduce every motor move to one step indefinitely.
- Add more persistence timers and special-case resume branches.
- Add many sample-specific UI tuning knobs.
- Train an opaque controller directly from the existing small run set.

These may change symptoms, but none separates mean stress, delayed actuation,
transformation drift, and measurement fluctuation.

## Recommended staged program

### Stage A: estimator in shadow mode

1. Implement a pure, dependency-light robust mean/drift and response estimator.
2. Feed recorded irregular raw-scale samples, current, motor commands,
   resistance, strain, and cadence into it.
3. Reproduce current controller events without changing them.
4. Log counterfactual mean, drift, uncertainty, response-pending state, and
   proposed action.

### Stage B: fitted closed-loop simulator

1. Fit separate hunting and transforming response/residual models.
2. Use contiguous residual blocks and actual cadence gaps.
3. Compare robust alpha-beta, Huber-Kalman, PI, and one-step predictive
   controllers.
4. Perform leave-one-loop-out and leave-one-run-out validation.

### Stage C: software-only controller boundary

1. Put estimator and policy in a pure control module.
2. Keep hardware, Qt, serial, PSU, and Tic imports out.
3. Preserve every hard safety path.
4. Add deterministic state/decision traces and rollback configuration.

### Stage D: guarded fixed-current hardware identification

Only after offline gates pass, use an approved campaign to test:

1. estimator-only shadow output;
2. one-command response identification with conservative limits;
3. fixed-current mean regulation;
4. short low-current ramp with probation;
5. full up/down recipe last.

### Stage E: full recipe acceptance

Require:

- no new safety stop or bypass;
- no recipe-rate overshoot;
- p95 stress error no worse than baseline by more than 5%;
- no increase in time outside the pause band;
- motor travel/reversals no worse than 5%;
- no material change in transformation onset, peak strain, or loop area;
- at least 25% lower hold time on hunting cases;
- no material regression on a real large-strain transforming hold-out.

## Recommended first comparison

The highest-value initial offline matrix is:

1. recorded/current baseline;
2. estimator diagnostics only;
3. estimator plus one-response budget;
4. estimator plus conservative PI;
5. estimator plus one-step predictive control;
6. each of 4 and 5 with disturbance observer;
7. winning force loop plus adaptive current slew;
8. winning force loop plus sensor fusion veto.

This matrix tests the underlying feedback architecture before adding recipe
shortcuts.

## Resume checkpoint

When this task resumes:

1. do not start from a new resume threshold;
2. inspect the latest main/controller changes and branch divergence;
3. use run 15 as the hunting/failure calibration;
4. use the finalized `Ni48Fe25Ga23Co4 1_7 iso-stress` run as a real
   transforming hold-out;
5. identify at least one calmer real run;
6. implement only the shadow estimator first;
7. do not touch hardware until the offline matrix passes and a new
   `campaign.yaml` is approved.
