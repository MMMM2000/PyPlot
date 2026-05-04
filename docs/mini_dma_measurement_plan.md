# Mini DMA Measurement Plan

This note is the working plan for turning Mini DMA from a manual bring-up logger into a practical measurement workflow for copper-wire tests and real microwires.

The detailed control-law reference for ramp speed, target seeking, live stiffness scaling, predictive corrections, and iso-load/iso-stress/iso-strain servo hold is `docs/mini_dma_speed_control.md`.

## What Already Works

- Scale communication: G&G balance on serial, including live real-gram reads and zero-load reference capture.
- Motion communication: Pololu Tic T500 through native PyUSB/libusb control transfers when available, with `ticcmd` as fallback. Position zero, jog, halt, keepalive, and recipe-driven position moves are routed through a persistent in-app dispatcher that coalesces stale target moves and lets closed-loop load/stress seeking continue from the last commanded target without waiting for a slower status poll.
- Supply communication: HMP4030/Owon-style SCPI supply, including current setpoint, output control, and measured voltage/current.
- `.pydpj` import: the Sample tab remembers the last Microwire Data Builder project, auto-imports diameter/current when the current naming fields match a row, and marks the diameter control red until the diameter came from the project. Manual diameter edits remain possible.
- Stress calculation: stress is calculated from effective load and wire diameter.
- Strain calculation: strain is calculated from displacement and `l0`, and can be delayed until the preload threshold is reached so slack take-up does not pollute strain zero.
- Logging: CSV includes elapsed time, raw Tic position, tensile-positive displacement, signed raw balance reading, positive applied tensile-load magnitude, preload state, strain, stress, current setpoint, measured current, voltage, resistance, power, and recipe context. Resistance is intentionally blank when the current is effectively zero.
- Existing recipes: displacement ramp, cyclic displacement, displacement hold, Hsw plateau scan, and separate iso-load, iso-stress, and iso-strain current sweeps.

## Current UI Map

- `Recipe`: normal bench operation, current sample reminder, recipe selection, per-recipe speed controls, global control/log/UI timing, zero-load reference capture, estimated points/duration, progress bar, auto-connect start button, and manual move/record actions.
- `Sample`: naming, diameter, `.pydpj` import, output folder, and base filename. The recipe-start setup measures the preloaded length and computes unloaded `l0`.
- `Hardware`: lower-priority scale, motor, power-supply, safety, and advanced serial/motor-driver settings for bring-up or troubleshooting.
- Right dashboard: live plot, run log, and plot presets. The duplicate status-bar echo is hidden so log lines only appear once.

The UI should stay operational and scan-friendly. The left settings panel must not horizontally scroll, and mouse-wheel scrolling over spin boxes or drop-downs must not silently change values.

## First Test Measurement

Use copper wire first, with conservative settings.

1. Confirm scale, Tic, and supply auto-detection.
2. Connect scale and supply, then use `Probe scale` and `Read supply now`.
3. Leave the balance showing real grams and set `Zero-load scale reading` to the unloaded hanging-weight reading, currently `21.200 g`, or use `Capture zero-load` only when the wire is at known `0 g` applied load.
4. For real microwire runs, the mandatory length setup first prompts for the approximate mounted wire length, uses it to scale the stiffness prior, then ramps to a small preload such as `10 MPa`. The setup popup shows live load/stress/displacement traces plus local pause/stop/progress controls, prompts for the measured gauge length at preload, returns to `0 g`, and computes `l0` from the measured length minus the tensile stage movement. If the return-to-zero stage reaches a stable near-zero raw balance plateau instead of the typed zero-load reference, it uses that plateau as the run's corrected zero reference and returns to the first plateau position for the `l0` calculation. Setup points are saved to `setup.txt` and `setup.csv` in the run folder. Setup stage speed is a motor-correction ceiling; before force starts responding, slack take-up can use that ceiling, and once the sample responds the requested `MPa/s` ramp is converted through the current stiffness estimate. Load/stress setup waits until each correction should be physically complete and then for fresh scale feedback before moving again, then bases the next correction on the last commanded target instead of the slower Tic status position. Correction-distance limits use the scale-feedback cadence rather than the faster UI/control timer.
5. Start with the `Iso-load current sweep` recipe. The copper setup currently uses quick bring-up targets `0, 3, 6, 9 g`, ramps between targets at a configurable load rate such as `0.1 g/s`, uses the target-ramp stage speed as the dynamic maximum for both target ramps and balancing, sweeps current from `1 mA` to a conservative low-current maximum and back at each load, then returns toward `0 g`. If the final return reaches a stable near-zero raw-balance plateau before the typed zero-load reference, Mini DMA accepts that plateau as the run's corrected zero reference and returns to the first plateau position instead of continuing to relax the stage.
6. Use tiny jogs to verify the load sign and motion direction. On the current rig, negative raw scale readings are treated as positive tensile load, so users should still type positive load targets.
7. Let `Start recipe (auto-connect)` preflight the scale and supply. For iso-load, iso-stress, and iso-strain current sweeps, the recipe controls current directly.
8. Run the current sweep below the copper-wire safety limit.
9. If the same base filename already exists, use `Save as next run` to keep the prior run folder and write the next `_run02`, `_run03`, and later repeat folder. Each folder contains `measurement.txt`, `measurement.csv`, `metadata.json`, `scale_raw.csv`, `setup.txt`, and `setup.csv`.
10. If the max-load safety limit is exceeded during setup, only tension-increasing moves are blocked; use the relaxing manual arrow to back away from the limit.
11. Stop the session and inspect CSV columns before using a microwire.

## Microwire Isostress Goal

Target experiment:

- At `0 MPa`, ramp current from a measurable non-zero baseline such as `1 mA` to `80 mA` and back to the same baseline.
- Repeat at `50 MPa`, `100 MPa`, `150 MPa`, and `200 MPa`.
- During each current sweep, continuously adjust stage position to keep stress constant; between stress levels, ramp the stress target at a controlled `MPa/s` rate instead of jumping directly to the next plateau.
- For nominal `0 MPa`, actively hold the zero-load scale reference instead of slackening the wire below zero, because slack/free transformation would make strain ambiguous. At the final return to zero, accept a stable near-zero raw-balance plateau as the corrected run baseline if continued relaxing no longer changes the balance reading.
- Scheduled CSV logging should never be the reason a long iso-load/stress run stops; if a row is due while the controller is still waiting for fresh post-move scale feedback, the row is deferred and the control loop continues.
- After a current ramp finishes, the settle step must reacquire the requested load/stress/strain target before the recipe advances to the next plateau; otherwise the next stress level would start from a mechanically wrong state.
- Load/stress target-ramp and hold movement should use one target-stage-speed ceiling, with dynamic balancing choosing the actual speed from target error, recent load/stress trend, stiffness, backlash state, and the 4-5 Hz force-feedback cadence.
- The detailed speed-control reference in `docs/mini_dma_speed_control.md` should be kept aligned with the code whenever ramp, seek, or hold logic changes.
- Near target, the controller should avoid immediate reversal if the target was crossed by less than the physical reversal band. With backlash and slow scale feedback, one small overshoot is better than repeatedly taking up backlash and hunting around the target.
- A wire break during a current sweep should stop immediately when the supply is at voltage limit and measured current collapses near zero, then offer displacement recovery instead of treating the event as a normal voltage-limit unwind.
- Log resistance, current, voltage, stress, strain, load, displacement, and phase/step labels.
- For the HMP4030, treat current as 0.2 mA-resolution setpoints below 1 A; the software should time the ramp from elapsed time and keep supply readbacks paced so reads do not slow current updates.

The important control loop is "hold target stress, sweep current." The stage changes strain as needed while the supply changes current.

## Needed Recipe Model

The current UI has parameterized built-in recipes. The next layer should be saved step recipes, probably as JSON files under a user-selected recipes folder.

Suggested step types:

- `connect`: verify expected scale/supply/Tic are reachable.
- `capture_zero_load`: record the current real balance reading as the `0 g` applied-load reference without changing the physical scale display.
- `set_gauge_zero`: set current motor position as the strain reference.
- `wait_for_preload`: wait until effective load exceeds a threshold, then set gauge zero.
- `set_current`: set a fixed current. The built-in `Controlled current sweep` recipe already uses this internally.
- `sweep_current`: ramp current start/end/rate while recording.
- `hold_control`: hold load, stress, strain, or position for duration/points.
- `move_relative`: move by a relative displacement with safety checks.
- `record`: record for duration or point count.
- `loop`: repeat a group over a list of targets.
- `stop_condition`: stop on fracture, voltage limit, max load, stale scale, or user stop.

For the isostress experiment, the saved recipe could be represented as:

```json
{
  "name": "Isostress 0-200 MPa, 1-80-1 mA",
  "sample_basis": "stress_mpa",
  "steps": [
    {"type": "tare_scale", "mode": "remote"},
    {"type": "wait_for_preload", "threshold_g": 0.02, "set_gauge_zero": true},
    {
      "type": "loop",
      "targets": [0, 50, 100, 150, 200],
      "body": [
        {"type": "hold_control", "basis": "stress_mpa", "target": "$target", "tolerance": 0.5, "settle_s": 1.0},
        {"type": "sweep_current", "start_mA": 1, "end_mA": 80, "ramp_rate_mA_s": 1, "record_interval_ms": 100, "hold_basis": "stress_mpa", "hold_target": "$target"},
        {"type": "sweep_current", "start_mA": 80, "end_mA": 1, "ramp_rate_mA_s": 1, "record_interval_ms": 100, "hold_basis": "stress_mpa", "hold_target": "$target"}
      ]
    }
  ]
}
```

## UI Direction

The most intuitive shape is a guided workflow rather than one long settings page:

- `Hardware`: hardware status, auto-detect, zero-load scale reference, gauge zero, diameter/project import, safety limits, with driver-level serial/motor details hidden under advanced settings.
- `Sample`: naming, `.pydpj` row match, diameter, `l0`, notes.
- `Program`: saved recipes, recipe preview, natural-language recipe preparation, and explicit step list.
- `Run`: large live controls, start/pause/stop, current target, stress/load/strain target, live plots, run log.
- `Review`: last run summary and quick open/export actions.

The operator should always see:

- whether stress is valid, which requires diameter and a fresh scale reading
- whether strain zero is pending or active
- whether current is actually flowing
- the active recipe step and stop condition
- a single obvious emergency stop/halt path that remains visible even while scrolling settings
- only routine operator controls by default; low-level hardware details and hardware cadence settings should stay in an advanced/config section
- preflight should auto-detect/connect required hardware before making run files and should report all missing devices together
- recipe estimates should be human-readable and paired with live progress
- numeric summaries should use compact values such as `20 g` and `0.01 mm` instead of padded zero-only decimals
- load/stress seeking should be sampled step-by-step: choose a speed and predicted correction from the current target error, move, wait for fresh scale feedback, log the feedback point with cached/commanded Tic position, then decide whether to move again. On the current G&G request/response scale, fresh force feedback is about 5 Hz, so load/stress correction sizes should budget speed over the scale interval, even if motor-side command tracking and UI updates run faster than status polling.
- load/stress seeking must not stop just because load stays flat while displacement increases; shape-memory transformations can elongate with little load change
- recipe current output should be off whenever a recipe is stopped or paused, while an optional HMP motor-supply channel can stay under explicit operator control for powering the Tic motor

## Next Implementation Priorities

1. Repeat the `Calibration` recipe on the real mounted geometry after major control changes. The latest valid calibration now seeds the live servo with stiffness/noise/backlash; stiffness is automatically rescaled when the current unloaded gauge length differs from the calibrated length, but representative lengths are still worth measuring because fixture compliance and wire transitions can change the real response.
2. Add saved recipe files and a previewable step list.
3. Add explicit saved-recipe `capture_zero_load` and recovery steps. The built-in current-sweep recipe uses the zero-load scale reference for load control, and manual stop now offers displacement/load recovery actions with a temporary dual-axis recovery plot.
4. Continue refining commercial-DMA-style guided workflow: Setup -> Program -> Run -> Review, with expert settings hidden unless needed.
5. Add a natural-language recipe preparation path that generates the same saved recipe JSON.
6. Add constant-current stress/strain recipes.
7. Add dynamic recipes such as increasing target stress until fracture, using load drop, rapid strain jump, or stale/invalid readings as stop conditions.
