# Mini DMA Logger

This note is the handoff for the `Mini DMA Logger` workflow so development can continue on another machine without relying on Codex chat memory.

## Purpose

`Mini DMA Logger` is a hardware-driven stress/strain and heating workflow for a small stepper-based tensile rig. The immediate target is shape-memory microwire work, with a second planned use case for automated `Hsw` distribution measurements under load.

The measurement roadmap, copper-wire bring-up plan, and saved-recipe design notes live in `docs/mini_dma_measurement_plan.md`. The current balance, actuator, and Tic controller assumptions live in `docs/mini_dma_hardware.md`.

The logger is intended to bring three subsystems into one session:

- motion control through the Pololu `Tic T500`
- force/load acquisition from the G&G balance over serial
- current annealing / electrical heating control in the same run

## Code Location

Primary implementation:

- `C:\Users\Martin\PyPlot\data_logging\mini_dma_logger\mini_dma_logger.py`

Integration / launcher entry:

- `C:\Users\Martin\PyPlot\launcher.py`

Related workflows reused as reference:

- `C:\Users\Martin\PyPlot\data_logging\current_annealing_logger\current_annealing_logger.py`
- `C:\Users\Martin\PyPlot\data_logging\manual_stress_strain_logger\manual_stress_strain_logger.py`
- `C:\Users\Martin\PyPlot\plotting/plugins\shape_memory_stress_strain\shape_memory_stress_strain_plugin.py`
- `C:\Users\Martin\PyPlot\plotting/plugins\dma_iso_stress\dma_iso_stress_plugin.py`

## Hardware Model

Current intended hardware stack:

- `Pololu Tic T500` stepper controller over USB, driven by `ticcmd`
- StepperOnline captive linear stepper actuator
- G&G balance over RS232 via USB serial adapter
- current annealing supply path, modeled after the existing current annealing logger

## What Is Already Implemented

### Core Session Model

- one combined Mini DMA session for motion, load, and recipe-owned current control
- session naming helpers and run notes
- settings-panel spin boxes and drop-downs ignore mouse-wheel value changes so scrolling the panel cannot silently alter recipe or hardware options
- the settings panel disables horizontal scrolling, and note/log text wraps to the available width
- hardware driver details such as baud rates, serial request commands, `ticcmd`, device serials, and steps-per-mm are hidden by default under `Advanced hardware settings`
- an always-visible `EMERGENCY STOP` button in the dashboard header stops the active recipe/session, halts the Tic motor, and turns the supply output off
- recipe start runs a preflight that auto-detects/connects required scale and supply hardware before creating run files, and reports all missing devices together
- the Recipe tab shows the current sample name and wire diameter at the top so the operator can catch stale sample identity or geometry before starting a run
- if output files already exist, session start can save the repeat measurement as the next `_run02`, `_run03`, ... filename instead of replacing the original files
- long recipe estimates switch from seconds to minutes/hours and the recipe panel includes a live progress bar
- numeric recipe summaries and spin boxes trim zero-only decimals, for example `20 g` instead of `20.0000 g`
- log messages appear in the `Run log`; the duplicate status-bar echo is hidden, and a Developer-menu mirror can write the run log to a rotating text file for debugging
- output to `TXT`, `CSV`, and `JSON` metadata sidecar
- each active session also writes a high-rate raw scale sidecar named `<run>.scale_raw.csv`, while the main CSV remains a slower recipe/session log
- recipe timing is split into a global control interval, global log interval, and UI refresh interval; individual recipes no longer own their own scheduler frequency
- the global timing controls live under `Settings -> Timing...` instead of taking space in the normal Recipe panel
- hardware communication keeps its own cadence: request-mode scale acquisition, Tic status, Tic command-timeout keepalive, and power-supply readbacks all have explicit timing settings, with supply readbacks still throttled so they do not block fast current commands
- the current G&G scale does not stream passively at `9600`; its measured `ESC+p` reply cadence is about 5 Hz, so Mini DMA defaults G&G request-mode acquisition to a 250 ms interval with a 300 ms read timeout and records every actual reply in the raw sidecar
- shape-memory-friendly export columns for `Displacement`, positive applied tensile `Load`, `Strain`, and `Stress`

### Motion

- `ticcmd` integration for Pololu Tic status and commands
- automatic Tic path / serial detection for the connected controller
- stale saved `ticcmd` paths are ignored in favor of a discovered local Pololu installation
- motor moves energize the Tic, reset its command timeout, and exit safe-start before sending the position target; a short keepalive continues resetting the command timeout during active recipes/manual holds
- Tic status shows VIN motor-supply voltage and warns/preflights when VIN is below the motor-power threshold
- displacement recipes expose their own linear move speed; the app schedules successive target positions from move distance and speed while the faster control loop stays available for logging, safety checks, and closed-loop decisions
- iso-load, iso-stress, and iso-strain current sweeps are separate recipe choices with target ramp rate, target ramp stage speed, conservative correction step, and correction move speed controls
- `Calibration` is a dedicated automatic recipe for mechanical characterization: after the operator mounts the wire and confirms zero-load/safety settings, Mini DMA runs the same mandatory length setup used by other recipes, measures baseline scale noise, seeks configured load preloads, performs forward/reverse micro-move sweeps, records labeled calibration phases, and saves stiffness/backlash plus stress-strain estimates into the JSON metadata
- clear stacked `Move up` / `Move down` buttons can be held for continuous manual jogging from the last commanded target, with held movement advancing by the configured linear `Manual move speed` in `mm/s`
- current-sweep recipes use an editable zero-load scale reference instead of physically taring the balance; the default hanging-weight reference is `21.200 g`
- every recipe now runs the length setup before normal recipe logging: ramp directly to the configured setup preload stress, prompt for the measured wire length at preload, return to `0 g` applied load, compute unloaded `l0` from the known tensile stage displacement, then start the normal recipe CSV/graph log
- the length-setup popup shows live load, stress, and tensile displacement versus setup time while it is chasing preload and returning to zero load; the popup has its own pause, stop, and progress controls
- setup has its own stage-speed ceiling and uses that speed together with the control interval to choose the net correction distance, so it is not limited by the fine calibration micro-move correction step
- position zeroing
- halt / stop support
- jog control refuses sub-step moves that would round to the current motor step
- displacement-driven automation recipes
- controlled current-sweep recipe that can hold load, stress, or strain while ramping current
- configurable soft position limits and max-load safety behavior; the zero-load hanging-weight reference is the default applied-load ceiling, an optional lower custom limit can stop earlier, and relaxing moves remain available when the limit is already exceeded so the rig is not trapped above the limit

### Scale

- serial port enumeration and selection
- G&G-oriented serial settings
- automatic scale-port detection based on a live G&G serial response
- scale probe / diagnostics
- one normal `Capture zero-load` action that records the current real balance reading as the `0 g` applied-load reference without changing the scale display
- optional session-start capture of the zero-load reference, for use only when the current raw balance reading is definitely unloaded
- applied tensile load is displayed and logged as the positive tensile magnitude in `Load` / `load_g` using `zero-load scale reading - current scale reading` for the current hanging-weight rig; signed raw balance remains available as `raw_load_g` for diagnostics
- the current G&G request/response scale on `COM6` at `9600` baud does not stream passively; it replies to `ESC+p` at about 5 Hz, so request-mode scale polling defaults to 250 ms and feeds a rolling signal buffer
- main CSV rows include interval load mean, standard deviation, min/max, sample count, and achieved scale sample rate
- raw scale sidecars preserve every real balance reading during a session with both raw grams and applied wire load, so transition fluctuations can be inspected without forcing the main log to run at the hardware polling rate
- tensile displacement uses its own motion-direction setting; the current rig defaults to negative raw Tic travel as positive tensile displacement, so the app can show/log positive `position_mm` while preserving raw Tic position as `raw_position_mm`
- physical remote tare and software tare are kept only in advanced hardware diagnostics because the normal workflow should leave the balance showing real grams

### Calibration Workflow

- The automatic `Calibration` recipe can be used with a stable non-transforming wire or, when needed, the installed microwire. Non-transforming wire is still better for pure backlash/stiffness checks because phase transitions do not add real material fluctuations.
- Physical setup is still operator-controlled: mount and align the wire, confirm the zero-load scale reference, soft limits, max-load safety, and mandatory length setup settings, then start the recipe.
- The recipe uses separate preload-seek and micro-move settings. The preload seek can be faster/coarser for a bent or slack calibration wire; the forward/reverse micro-move step stays small for stiffness and backlash characterization.
- The default preload range is conservative for a short microwire. For a stiff 0.12 mm copper wire, raise the preload range and seek speed only as needed to make the wire visibly straight.
- The recipe records `calibration_baseline`, `calibration_preload`, `calibration_forward`, and `calibration_reverse` phases in the main CSV.
- Completion computes a calibration report from the session points and stores it under `calibration.report` in the JSON metadata; `copper_calibration` is retained as a legacy alias for old output readers.
- The report includes baseline load noise, forward and reverse load-path stiffness in `g/mm`, average stiffness, estimated backlash in `mm`, optional stress-strain modulus in MPa/GPa when length/diameter are available, and sample counts.
- The measured backlash is reported for review; it is not silently applied to the live backlash setting.

### Heating / Current Annealing

- integrated heating subsystem in the same logger
- automatic supply-port detection for supported SCPI supplies
- live current / voltage / resistance / power channels
- recipe-owned current workflows instead of a separate hardware-tab heating program
- HMP4030 users can optionally assign CH1 or CH2 as the motor-supply channel; recipe preflight turns that channel on before checking Tic VIN, while current annealing remains on the configured annealing channel
- HMP4030 current commands are treated as 0.2 mA-resolution setpoints below 1 A, so a `1 mA/s` ramp can update in smaller timed increments while avoiding unsupported command precision
- the main tabs are organized as `Recipe`, `Sample`, and lower-priority `Hardware`, so scale/motor/power-supply setup does not dominate routine use
- iso-load, iso-stress, and iso-strain current sweeps own both the target ramp and current ramp directly, advance current from elapsed time, and are shown as separate recipe modes; the progress bar estimates timed target/current-ramp ticks instead of only counting visible recipe rows
- if a current sweep reaches the configured voltage limit before reaching the requested current, Mini DMA ramps the recipe current back to that sweep's start current, records that unwind phase, and advances to the next recipe step instead of stopping the whole recipe
- the mandatory length setup actively returns to the `21.200 g` zero-load reference after the setup preload, so a nominal `0 MPa` sweep remains an active zero-load/zero-stress measurement with strain still defined
- stress-based recipe fields show their corresponding load values next to the controls, and load-based recipe fields show their corresponding stress values using the current diameter
- closed-loop target seeking samples feedback during each correction/settle/current step, but scheduled main CSV rows are throttled by the separate log interval; static seeking still avoids stacking commands ahead of confirmed position, while target ramps such as setup preload can advance the planned motion target between scale updates for smoother movement. Target seeking adapts correction step and speed near the target, keeps correcting inside the broad hold tolerance toward a tighter near-target band, detects target overshoot, switches to fine reverse correction steps, and can apply a measured backlash take-up distance on direction reversals
- recipe completion stops the session log before running the return-to-start recovery popup; recipe stop/fault turns current annealing output off, keeps a resume point, and can ask whether to move displacement or load back toward zero without appending recovery samples to the recipe CSV; paused recipes also turn current output off until resumed
- resistance is left blank when the current setpoint/measured current is effectively zero, avoiding invalid zero-current resistance points; supply readbacks are throttled during fast automation so current commands do not fight voltage/current queries on the same serial link
- behavior patterned after the existing current annealing logger rather than as a separate app

### Shape-Memory Workflow

- unloaded gauge length `l0` is computed by the mandatory recipe-start preload/return setup instead of being typed in the Sample tab
- pre-contact / straightening phase is handled before normal recipe logging starts
- `.pydpj` import support for sample naming and diameter; the last project path and naming fields are restored, matching rows auto-import the wire diameter, and the diameter control is marked red until the value has been imported from the Builder project
- stress calculation from imported diameter when available

### Plotting / UI

- dark-theme-aware Matplotlib styling
- configurable 4-tile dashboard instead of a fixed graph trio
- selectable plot channels with left/right axis support
- recovery actions open a temporary dual-axis load/displacement vs time graph while returning load or displacement toward zero/start, and the same actions are available from `Manual Actions`
- plot configuration moved into a popup dialog instead of taking permanent dashboard space
- collapsible `Overview` section with remembered expanded/collapsed state

### Hsw Distribution Workflow

There is already a first implementation scaffold for automated `Hsw` distribution measurements:

- recipe mode: `Hsw distribution`
- target basis can be:
  - `Load (g)`
  - `Stress (MPa)`
  - `Strain (%)`
- configurable:
  - start / end / step
  - tolerance
  - stage correction step in `mm`
  - points per plateau
  - plateau settle time
  - optional reverse sweep
- per-point export metadata for later analysis:
  - `recipe_mode`
  - `automation_phase`
  - `automation_basis`
  - `automation_target_value`
  - `plateau_index`
  - `plateau_label`
- JSON metadata carries the Hsw recipe settings
- auto-generated Hsw-oriented filename suffixes based on basis and sweep settings

## UI Decisions Already Made

These were intentional product decisions, not random implementation details:

- keep shape-memory and heating in one combined logger rather than launching separate tools
- compute strain zero from the mandatory preload/return length setup instead of blindly treating the initial stage position as strain zero
- keep graph setup configurable, but move the controls into a popup so the run dashboard stays clean
- make `Overview` collapsible because it is useful context but should not dominate the working layout
- support both shape-memory and Hsw workflows in one app, with recipes deciding behavior

## Current Risks And Validation Gaps

The following are still pending live validation on the real rig:

- real end-to-end motion + load + heating runs
- validation of Hsw plateau-seeking on live force feedback
- final tuning of safety thresholds against the real mechanics

## What Was Verified In Software

The implementation was exercised with synthetic and smoke-level checks, including:

- launcher smoke checks
- Mini DMA window construction
- session export generation
- recipe-loop smoke tests for displacement workflows
- synthetic Hsw recipe runs
- `.pydpj` import path
- mandatory preload/return length setup logic
- plot/dashboard visual review iterations

This means the code structure and main workflows were developed beyond pure scaffolding, but full recipe behavior still needs hardware validation with real motion, load, and heating together.

## What Was Verified On Real Hardware

The latest bench session confirmed that the app-level assumptions now match the actual rig wiring:

- Pololu Tic T500 reachable through `ticcmd` with serial `00501366`
- G&G balance replying on `COM6` at `9600` baud with `ESC+p`; a 12 s request/response benchmark measured 60 samples, 4.94 Hz, about 202 ms/sample, and 0 timeouts
- G&G live readout working while the balance remains in real grams
- HAMEG `HMP4030` replying on `COM3` at `115200` baud
- HMP4030 channel `3` sourcing real current through the microwire path at about `15 mA`
- closed-loop motion against live scale feedback uses the zero-load scale reference so, for example, a `21.200 g` zero-load reading and `18.200 g` live reading is reported/logged as about `+3 g` applied tensile load

This means the main remaining risk is not basic communications anymore, but day-to-day measurement workflow polish, safeguards for real sample runs, and the low-bandwidth nature of the balance feedback.

## Recommended First Steps On Another Machine

When resuming work elsewhere:

1. Check out the branch or merge commit that contains the Mini DMA work.
2. Open this file first.
3. Confirm the project `.venv` is healthy and use it for all Python commands.
4. Verify `ticcmd` is installed and reachable on that machine.
5. Use the built-in auto-detect actions to classify the Tic, scale, and supply ports if the COM numbering changed.
6. Reconnect the rig and test the motion side first with tiny jogs.
7. Re-test the scale link and expect the current G&G request/response path to be about 5 Hz, not 20 Hz.
8. Use the built-in `Probe scale` action before trying full automated runs.
9. Once the scale is live, run one simple shape-memory measurement before tuning Hsw automation further.

## Recommended First Live Validation Sequence

For the current bench rig:

1. Open `Mini DMA Logger`.
2. Keep `Pololu Tic Control Center` closed while Mini DMA is using the controller.
3. Use `Check Tic` and a tiny jog to confirm motion.
4. Use `Probe scale` and confirm live readings arrive from the balance at the expected roughly 5 Hz request/response cadence.
5. Verify `Capture zero-load` or type the known zero-load balance reading.
6. Run a minimal preload-only shape-memory test.
7. Run a short displacement recipe with logging.
8. Only after that, try the `Hsw distribution` recipe with very conservative settings.

## What Context Will Not Transfer Automatically

The other machine will have:

- the code
- the PR / commit history
- this handoff note

The other machine will not automatically have:

- the full Codex chat reasoning
- remembered discussion about why certain UI choices changed
- remembered details about the scale cabling investigation unless they are written down

That is why this file exists: it captures the important product and implementation context so follow-up work does not depend on cross-device chat memory.

## Suggested Next Development Priorities

After basic live communication, the natural next priorities are:

1. validate the full shape-memory workflow end-to-end on real hardware
2. tune strain/stress zeroing and safety thresholds from real runs
3. validate recipe-owned current runs
4. harden the Hsw automation loop against overshoot and noisy feedback
5. add later analysis or plotting helpers for plateau-based Hsw results if needed
