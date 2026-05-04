# Mini DMA Speed Control

This document describes how Mini DMA chooses motor speed and correction distance during displacement ramps, load/stress/strain target seeking, setup preload, and iso-load/iso-stress/iso-strain current sweeps.

It is meant to be the operator-facing source of truth for the control logic. If code behavior changes, update this file in the same change.

## Hardware Limits

The Mini DMA rig has two different clocks:

- **Motor command clock:** Tic commands can be sent quickly, especially through the persistent native-USB/dispatcher path.
- **Force-feedback clock:** the current G&G balance replies to request/response reads at about 4-5 Hz, so fresh load/stress feedback is roughly every 200-250 ms.

This means load/stress control must be sample-driven. The app can keep the UI responsive faster than that, but it should not stack multiple force corrections from the same old scale value.

![Sample-driven load/stress correction clock](assets/mini_dma_scale_sample_timing.svg)

## Quantities

Mini DMA keeps several related speed and sensitivity terms:

| Term | Meaning |
| --- | --- |
| `speed_mm_s` | Motor/stage speed command in mm/s. |
| `target_rate` | Requested recipe ramp rate in g/s, MPa/s, or %/s. |
| `basis` | Controlled quantity: load `g`, stress `MPa`, strain `%`, or position `mm`. |
| `error` | `target_value - measured_value` in the selected basis. |
| `effective_tolerance` | Requested tolerance raised to what the rig can physically resolve. |
| `sensitivity` | Current estimate of how strongly the sample responds to motion, e.g. g/mm, MPa/mm, or %/mm. |
| `decision_interval` | Expected time between meaningful load/stress decisions, usually the scale request interval. |
| `max_speed` | User-facing maximum motor speed for the active mode. |

## Position Ramps

Displacement-only recipes are open-loop position motion. The target position is known from the recipe, so Mini DMA schedules motion by distance and the configured displacement speed:

```text
move_duration_s = abs(target_position_mm - start_position_mm) / speed_mm_s
```

The motor may receive planned position updates between force samples because displacement ramps do not need fresh scale feedback to know where the target is.

## Load / Stress / Strain Seeking

Load, stress, and strain seeking are closed-loop. A correction cycle is:

1. Read the latest fresh feedback value.
2. Compute error in the controlled basis.
3. Compute an effective tolerance.
4. Estimate or reuse sensitivity.
5. Predict a correction distance.
6. Choose a speed.
7. Apply backlash/reversal rules.
8. Send one motor move.
9. Wait until the commanded move should be physically complete, add a small settle margin, then wait for fresh scale feedback before deciding again.

Step 9 is important. A balance reply that arrives after the command was sent is not enough if the stage is still moving. Otherwise the controller can stack several corrections before the wire has actually responded, which appears as large near-target jumps.

## Effective Tolerance

Load/stress target tolerance is automatic. Mini DMA starts from a small requested load floor of `0.005 g`, converts it to stress when the active basis is MPa, and then raises that request when the hardware cannot physically resolve something tighter.

```text
requested_load_floor = 0.005 g
step_floor = abs(sensitivity) * motor_step_mm
noise_floor = calibrated_load_noise_g * 3  (converted to MPa when needed)
effective_tolerance = max(requested_load_floor_in_current_basis, step_floor, noise_floor)
```

For example, if one motor step changes stress by 2 MPa, a 0.25 MPa tolerance is not physically meaningful, and neither is the 0.005 g starting floor. The controller must treat a roughly step-sized band as the realistic target region. This is why the operator should not need to retune tolerance for every wire length; the calibrated/live stiffness and motor step size set the real floor.

## Stiffness And Sensitivity

The calibration recipe stores load stiffness in g/mm:

```text
calibrated_stiffness_g_per_mm
calibrated_length_mm
```

For a different mounted wire length, Mini DMA rescales it:

```text
current_stiffness_g_per_mm =
    calibrated_stiffness_g_per_mm * calibrated_length_mm / current_l0_mm
```

Stress sensitivity is calculated from load stiffness and wire diameter:

```text
sensitivity_MPa_per_mm =
    stress_mpa_from_load_g(current_stiffness_g_per_mm, diameter_mm)
```

Strain sensitivity is geometric:

```text
sensitivity_pct_per_mm = 100 / l0_mm
```

During a run, Mini DMA can also update live stiffness from recent observed response:

```text
observed_sensitivity = abs(delta_value / delta_effective_displacement_mm)
live_stiffness = (1 - alpha) * old_live_stiffness + alpha * observed_stiffness
```

Only moves large enough to be meaningful are used. Tiny moves below about half a motor step are ignored.

The live stiffness is kept both for the exact target currently being chased and as a run-level stiffness estimate. That matters during ramps, where the desired target changes every tick. Without the run-level estimate, the controller would forget what it learned at intermediate ramp targets and then behave as if stiffness were unknown at the final settle.

## Predictive Correction Distance

When sensitivity is available, Mini DMA estimates the correction distance:

```text
predicted_move_mm = correction_gain * abs(error) / abs(sensitivity)
```

Then it clamps the move:

```text
max_move_mm = max_speed_mm_s * decision_interval_s
correction_move_mm = clamp(predicted_move_mm, motor_step_mm, max_move_mm)
```

This is why speed and correction distance are connected. A max speed of 1 mm/s with a 250 ms scale interval means one force-feedback correction should normally not exceed about 0.25 mm.

## Generic Smooth Landing

For non-current-sweep seeking without sensitivity-specific behavior, Mini DMA uses a smooth landing zone. Far from target it can use full speed; near target it slows down to the minimum useful motor speed.

![Generic seek speed vs target error](assets/mini_dma_smooth_landing_speed.svg)

The generic shape is:

```text
error_ratio = abs(error) / effective_tolerance

if error_ratio >= full_speed_error_ratio:
    speed = max_speed
else:
    scaled = clamp((error_ratio - 1) / (full_speed_error_ratio - 1), 0, 1)
    smooth = smoothstep(scaled)
    speed = max_speed * smooth
```

Inside tolerance, the target is reached and no correction is sent.

## Current-Sweep Servo Hold

During iso-load, iso-stress, and iso-strain current sweeps, the motor must keep balancing while current changes the sample. This is not a fixed correction-step controller anymore. The visible setting is:

```text
Target ramp stage speed = absolute max motor speed
```

The actual speed below that ceiling is dynamic:

```text
away_rate = max(0, -sign(error) * measured_value_rate)

requested_value_rate =
    K_error * abs(error)
  + K_away * away_rate

speed_mm_s =
    requested_value_rate / abs(sensitivity)

speed_mm_s = clamp(speed_mm_s, minimum_speed, target_ramp_stage_speed)
```

This means:

- larger error increases speed;
- if load/stress is moving away from the target, speed gets an extra boost;
- stiffer samples move slower for the same stress error because a small displacement changes stress a lot;
- more compliant or longer samples can move faster without overshooting as much;
- the user still has one hard safety ceiling in mm/s.
- the same smooth landing cap applies near target, so a high ceiling such as 5 mm/s is not used right outside the hold band.

The dashboard header displays the most recent commanded speed in fixed-width cells:

```text
mm/s, g/s, MPa/s, %/s
```

The g/s and MPa/s values use the current live stiffness estimate when available, otherwise the length-scaled calibration prior. The %/s value uses the current `l0`.

![Iso-stress/current-sweep dynamic balance speed](assets/mini_dma_current_sweep_servo_speed.svg)

## Recipe Ramp Rates In g/s, MPa/s, And %/s

When a recipe says `1 MPa/s` or `0.1 g/s`, that is a target-value ramp rate, not directly a motor speed.

Mini DMA converts it through sensitivity:

```text
speed_cap_from_ramp = target_rate_value_per_s / abs(sensitivity_value_per_mm)
```

Examples:

- `1 MPa/s` with `20 MPa/mm` sensitivity becomes `0.05 mm/s`.
- `0.1 g/s` with `2 g/mm` sensitivity becomes `0.05 mm/s`.
- `1 %/s` with `3.33 %/mm` sensitivity becomes `0.30 mm/s`.

The final motor speed is the smaller of the recipe's max mm/s and any active target-ramp speed cap, except during slack take-up where the sample has not started responding yet.

## Setup Preload

Setup preload is special because the wire may initially be slack or bent.

The setup sequence is:

1. Ask for approximate starting length.
2. Use that length to rescale the stiffness prior.
3. Move toward the setup preload target.
4. Once the wire responds, respect the requested `MPa/s` target ramp through stiffness.
5. Ask for measured length at preload.
6. Return toward zero load and compute `l0`.

Before force starts responding, Mini DMA can use `Setup stage speed` for slack take-up. Tiny residual loads near zero are still treated as slack take-up, because a long or bent wire can show a few milligrams of apparent load before it is meaningfully straight. Once applied load rises above the slack-take-up threshold, setup uses the stiffness/ramp-rate model. If stiffness is still unknown near target, the fallback correction also uses the smooth landing curve; near target it sends one motor step at the minimum motor speed instead of a full stage-speed-sized correction.

The setup points are saved to `setup.csv` and `setup.txt` in the run folder. If setup jumps or oscillates, inspect `setup.csv` first.

During the post-preload return to zero, Mini DMA watches for a stable raw-balance plateau. If the motor keeps relaxing but the raw balance only fluctuates inside a small flat band, the controller uses the center of that raw band as the corrected zero-load reference for the current run and returns to the first plateau position before computing `l0`. The same plateau fallback is used by final zero-load return and manual load-zero recovery.

## Backlash And Reversal Rules

Backlash take-up is real motor travel that should not count as specimen displacement. Mini DMA therefore keeps:

- raw motor position;
- effective specimen displacement;
- backlash take-up travel excluded from strain.

Direction reversal is expensive near target. Mini DMA should not reverse just because one sample crossed the target by less than the physical reversal band.

The reversal band is based on:

```text
effective_tolerance
motor_step_mm * sensitivity
backlash_mm * sensitivity
```

If a correction crosses the target but the remaining error is inside this band, Mini DMA accepts the target as reached instead of reversing immediately. This prevents backlash-driven hunting.

## Current Limitations

The current controller is still mostly feedback-based. It compensates for large error and moving-away trends, but it does not yet have full current feed-forward.

The planned next layer is latency/feed-forward prediction:

```text
predicted_error =
    current_error
  + measured_value_rate * scale_latency_s
  + expected_current_effect
```

Then:

```text
requested_value_rate =
    Kp * predicted_error
  + Kd * away_rate
  + feedforward_from_current_ramp
```

This matters during current annealing because the wire can contract quickly as current rises. Feed-forward would let the motor start compensating before the delayed scale feedback shows the full stress increase.

## What To Check In A Run

For debugging speed control:

1. Open the run folder.
2. Inspect `setup.csv` for pre-measurement preload behavior.
3. Inspect `measurement.csv` for recipe behavior.
4. Inspect `scale_raw.csv` for real balance sample timing and noise.
5. Compare commanded displacement changes to load/stress changes.
6. Check whether oscillations start at direction reversals, after stiffness updates, or during current-induced transitions.

Important symptoms:

| Symptom | Likely meaning |
| --- | --- |
| Slow movement before any load change | Slack take-up or stiffness prior too conservative. |
| Huge overshoot after first load response | Sensitivity too low, correction distance too large, or scale lag. |
| Repeated target crossing around preload | Reversal/backlash band too small or speed too aggressive near target. |
| Stress rises during current ramp while displacement lags | Need higher max speed, stronger away-rate gain, or current feed-forward. |
| Load stops changing while motor keeps moving toward zero | Zero-load plateau fallback should accept the new baseline. |

## Operator Rule Of Thumb

Use one visible max speed as the safety ceiling. Let the controller choose the actual speed below that ceiling from error, stiffness, scale timing, and backlash.

For long, compliant wires, a higher max speed may be safe because the same motor step changes stress less. For short, stiff wires, the same mm/s can produce large MPa jumps.
