# Mini DMA Measurement Plan

This note is the working plan for turning Mini DMA from a manual bring-up logger into a practical measurement workflow for copper-wire tests and real microwires.

## What Already Works

- Scale communication: G&G balance on serial, including live reads and remote tare (`ESC+t`).
- Motion communication: Pololu Tic T500 through `ticcmd`, including position zero, jog, halt, and recipe-driven position moves.
- Supply communication: HMP4030/Owon-style SCPI supply, including current setpoint, output control, and measured voltage/current.
- `.pydpj` import: the Specimen tab can load a Microwire Data Builder project and fill sample metadata, diameter, and current when a matching row is found.
- Stress calculation: stress is calculated from effective load and wire diameter.
- Strain calculation: strain is calculated from displacement and `l0`, and can be delayed until the preload threshold is reached so slack take-up does not pollute strain zero.
- Logging: CSV includes elapsed time, position, raw/effective load, preload state, strain, stress, current setpoint, measured current, voltage, resistance, power, and recipe context.
- Existing recipes: position ramp, cyclic triangle, position hold, and Hsw distribution by load, stress, or strain.

## Current UI Map

- `Connections`: scale, motor, safety limits, supply connection, manual current, and live supply readout.
- `Heating`: heating program settings used during session recording, currently off/constant/ramp/triangle.
- `Specimen`: naming, gauge length, diameter, preload zeroing, `.pydpj` import, output folder, session start/stop.
- `Recipes`: simple recipe type selection and manual setup actions.
- Right dashboard: live plot, run log, and plot presets.

The UI should stay operational and scan-friendly. The left settings panel must not horizontally scroll, and mouse-wheel scrolling over spin boxes or drop-downs must not silently change values.

## First Test Measurement

Use copper wire first, with conservative settings.

1. Confirm scale, Tic, and supply auto-detection.
2. Connect scale and supply, then use `Probe scale` and `Read supply now`.
3. Use `Remote tare scale`.
4. Set a known copper-wire diameter and `l0` manually.
5. Start a session with heating off.
6. Use tiny jogs to verify the load sign and motion direction.
7. Run a small load-controlled or displacement-controlled test below the copper-wire safety limit.
8. Turn on a small current manually and confirm measured current, voltage, and resistance are logged.
9. Stop the session and inspect CSV columns before using a microwire.

## Microwire Isostress Goal

Target experiment:

- At `0 MPa`, sweep current from `0 mA` to `80 mA` and back to `0 mA`.
- Repeat at `50 MPa`, `100 MPa`, `150 MPa`, and `200 MPa`.
- During each current sweep, continuously adjust stage position to keep stress constant.
- Log resistance, current, voltage, stress, strain, load, displacement, and phase/step labels.

The important control loop is "hold target stress, sweep current." The stage changes strain as needed while the supply changes current.

## Needed Recipe Model

The current UI has parameterized built-in recipes. The next layer should be saved step recipes, probably as JSON files under a user-selected recipes folder.

Suggested step types:

- `connect`: verify expected scale/supply/Tic are reachable.
- `tare_scale`: send remote tare or apply software tare.
- `set_gauge_zero`: set current motor position as the strain reference.
- `wait_for_preload`: wait until effective load exceeds a threshold, then set gauge zero.
- `set_current`: set a fixed current.
- `sweep_current`: sweep current start/end/step while recording.
- `hold_control`: hold load, stress, strain, or position for duration/points.
- `move_relative`: move by a relative displacement with safety checks.
- `record`: record for duration or point count.
- `loop`: repeat a group over a list of targets.
- `stop_condition`: stop on fracture, voltage limit, max load, stale scale, or user stop.

For the isostress experiment, the saved recipe could be represented as:

```json
{
  "name": "Isostress 0-200 MPa, 0-80-0 mA",
  "sample_basis": "stress_mpa",
  "steps": [
    {"type": "tare_scale", "mode": "remote"},
    {"type": "wait_for_preload", "threshold_g": 0.02, "set_gauge_zero": true},
    {
      "type": "loop",
      "targets": [0, 50, 100, 150, 200],
      "body": [
        {"type": "hold_control", "basis": "stress_mpa", "target": "$target", "tolerance": 0.5, "settle_s": 1.0},
        {"type": "sweep_current", "start_mA": 0, "end_mA": 80, "step_mA": 1, "record_interval_ms": 100, "hold_basis": "stress_mpa", "hold_target": "$target"},
        {"type": "sweep_current", "start_mA": 80, "end_mA": 0, "step_mA": 1, "record_interval_ms": 100, "hold_basis": "stress_mpa", "hold_target": "$target"}
      ]
    }
  ]
}
```

## UI Direction

The most intuitive shape is a guided workflow rather than one long settings page:

- `Setup`: hardware status, auto-detect, scale tare, gauge zero, diameter/project import, safety limits.
- `Sample`: naming, `.pydpj` row match, diameter, `l0`, notes.
- `Program`: saved recipes, recipe preview, natural-language recipe preparation, and explicit step list.
- `Run`: large live controls, start/pause/stop, current target, stress/load/strain target, live plots, run log.
- `Review`: last run summary and quick open/export actions.

The operator should always see:

- whether stress is valid, which requires diameter and a fresh scale reading
- whether strain zero is pending or active
- whether current is actually flowing
- the active recipe step and stop condition
- a single obvious emergency stop/halt path

## Next Implementation Priorities

1. Add no-horizontal-scroll UI polish and keep the settings page safe to scroll.
2. Make tare visible in setup/manual controls and add `tare_scale` as a recipe step.
3. Add saved recipe files and a previewable step list.
4. Add an isostress current-sweep recipe type using stress or load as the hold basis.
5. Add a natural-language recipe preparation path that generates the same saved recipe JSON.
6. Add isostrain and constant-current stress/strain recipes.
7. Add dynamic recipes such as increasing target stress until fracture, using load drop, rapid strain jump, or stale/invalid readings as stop conditions.
