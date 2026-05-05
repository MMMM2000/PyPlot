# Mini DMA Logger

This note is the handoff for the `Mini DMA Logger` workflow so development can continue on another machine without relying on Codex chat memory.

## Purpose

`Mini DMA Logger` is a hardware-driven stress/strain and heating workflow for a small stepper-based tensile rig. The immediate target is shape-memory microwire work, with a second planned use case for automated `Hsw` distribution measurements under load.

The measurement roadmap, copper-wire bring-up plan, and saved-recipe design notes live in `docs/mini_dma_measurement_plan.md`. The current balance, actuator, and Tic controller assumptions live in `docs/mini_dma_hardware.md`. The detailed ramp, target-seeking, and servo-hold speed-control reference lives in `docs/mini_dma_speed_control.md`.

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

- `Pololu Tic T500` stepper controller over USB, preferably driven by the native PyUSB/libusb backend with `ticcmd` kept as fallback
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
- the dashboard header shows fixed-width live cells for session state, load/stress/strain, command speed in `mm/s`, `g/s`, `MPa/s`, `%/s`, and hardware status, so changing numbers no longer shift the layout
- recipe start runs a preflight that auto-detects/connects required scale and supply hardware before creating run files, and reports all missing devices together
- the Recipe tab shows the current sample name and wire diameter at the top so the operator can catch stale sample identity or geometry before starting a run
- if output for the same base name already exists, session start can save the repeat measurement in the next `_run02`, `_run03`, ... run folder instead of replacing the original run
- long recipe estimates switch from seconds to minutes/hours and the recipe panel includes a live progress bar
- numeric recipe summaries and spin boxes trim zero-only decimals, for example `20 g` instead of `20.0000 g`
- log messages appear in the `Run log`; the duplicate status-bar echo is hidden, and a Developer-menu mirror can write the run log to a rotating text file for debugging
- each run is written to its own output folder containing `measurement.txt`, `measurement.csv`, `metadata.json`, `scale_raw.csv`, `setup.txt`, and `setup.csv`
- `measurement.csv` remains the slower recipe/session log, `scale_raw.csv` preserves every acquired balance reply, and `setup.csv` records pre-measurement setup separately from the recipe data
- saved or typed base filenames with repeated `_run02` / `_run03` suffix chains are cleaned back to the base sample name before choosing the next run folder, and the output-folder row includes an `Open` button next to `Browse`
- recipe timing is split into a global control interval, global log interval, and UI refresh interval; individual recipes no longer own their own scheduler frequency
- the global timing controls live under `Settings -> Timing...` instead of taking space in the normal Recipe panel
- hardware communication keeps its own cadence: request-mode scale acquisition, Tic status, Tic command-timeout keepalive, and power-supply readbacks all have explicit timing settings, with supply readbacks still throttled so they do not block fast current commands
- Tic command state is tracked separately from slower Tic status polling: recipes and calibration micro-moves chain from the last commanded target, while scheduled data-log rows use cached/commanded position instead of forcing a blocking Tic status subprocess
- Tic move, halt, zero-position, and keepalive commands go through a persistent in-app command dispatcher that coalesces superseded target-position commands and drains halt/zero requests before the UI reports them complete; when PyUSB/libusb can open the Tic, the dispatcher uses native USB control transfers and otherwise falls back to `ticcmd`
- the current G&G scale does not stream passively at `9600`; its measured `ESC+p` reply cadence is about 5 Hz, so Mini DMA defaults G&G request-mode acquisition to a 250 ms interval with a 300 ms read timeout and records every actual reply in the raw sidecar
- shape-memory-friendly export columns for `Displacement`, positive applied tensile `Load`, `Strain`, and `Stress`

### Motion

- native PyUSB/libusb integration for Pololu Tic status and commands, with `ticcmd` fallback
- automatic Tic path / serial detection for the connected controller
- stale saved `ticcmd` paths are ignored in favor of a discovered local Pololu installation; the advanced motor settings also include a native-USB preference checkbox
- motor moves energize the Tic, reset its command timeout, and exit safe-start before sending the position target; a short keepalive continues resetting the command timeout during active recipes/manual holds
- Tic status shows VIN motor-supply voltage and warns/preflights when VIN is below the motor-power threshold
- displacement recipes expose their own linear move speed; the app schedules successive target positions from move distance and speed while the faster control loop stays available for logging, safety checks, and closed-loop decisions
- iso-load, iso-stress, and iso-strain current sweeps are separate recipe choices with target ramp rate, stage speed cap, correction strain cap, and correction strain-rate cap controls; current-sweep balance corrections choose speed dynamically under the `%/s` and `mm/s` ceilings, while the old correction step/speed controls are hidden legacy settings kept only for compatibility with saved profiles
- `Calibration` is a dedicated automatic recipe for mechanical characterization: after the operator mounts the wire and confirms zero-load/safety settings, Mini DMA runs the same mandatory length setup used by other recipes, measures baseline scale noise, seeks configured load preloads, performs forward/reverse micro-move sweeps, records labeled calibration phases, saves stiffness/backlash plus stress-strain estimates into the JSON metadata, and keeps the latest valid stiffness/noise/backlash as the live control prior
- clear stacked `Move up` / `Move down` buttons use true press-and-hold jogging from the last commanded target, with held movement advancing by the configured linear `Manual move speed` in `mm/s`; short clicks still use the single-click jog step
- `Manual Actions` includes an auto-connect hardware button so the motor and scale can be prepared for setup moves before a recipe is started
- current-sweep recipes use an editable zero-load scale reference instead of physically taring the balance; the default hanging-weight reference is `21.200 g`
- every recipe now runs the length setup before normal recipe logging: prompt for the approximate mounted wire length so the stiffness prior is scaled before motion starts, ramp to the configured setup preload stress, prompt for the measured wire length at preload, return to `0 g` applied load, compute unloaded `l0` from the known tensile stage displacement, then start the normal recipe CSV/graph log
- the length-setup popup shows live load, stress, and tensile displacement versus setup time while it is chasing preload and returning to zero load; the popup has its own pause, stop, and progress controls
- every length-setup point is also written to the run folder's `setup.txt` and `setup.csv`, so setup/preload behavior can be inspected without mixing those points into the main recipe log
- if the return-to-zero stage sees the raw balance reading stop changing near the zero-load reference while the motor keeps relaxing, Mini DMA treats the center of that stable raw-reading band as this run's zero-load reference, returns to the first position where the plateau appeared, and uses that position for the unloaded `l0` calculation
- motor displacement calibration now defaults provisionally to `800 steps/mm`, matching a 100 full-step/mm actuator at 1/8 microstepping; old saved profiles that still contain the previous `100 steps/mm` default are migrated to `800 steps/mm`, while custom values are preserved
- the advanced motor settings include a motor step calibration workflow for external gauges: after the operator manually takes up backlash and enters a baseline reading, Mini DMA keeps a progress window open, keeps the Tic command-timeout reset active during slow calibration moves, moves farther down by raw Tic position units, prompts for each gauge reading, writes CSV/JSON logs, fits steps/mm with residual/R2 diagnostics, and defaults to saving the result without applying it
- setup zero-load tolerance is automatic from the same `0.005 g` load floor plus motor-step/noise limits used by the rest of load/stress seeking; the old manual zero-load tolerance field is hidden
- setup has its own stage-speed ceiling and, for load/stress feedback moves, uses the scale feedback interval to choose the net correction distance, so it is not limited by the 50 ms control tick or the calibration micro-move step
- load/stress setup continues correction moves from the last commanded target after each fresh post-move scale sample, even if Tic status polling has not caught up yet, preventing slow status refreshes from turning valid correction steps into repeated `Move skipped` messages
- speed-control behavior, including unit conversion for `g/s`, `MPa/s`, `%/s`, live stiffness scaling, predictive correction distance, smooth landing speed, current-sweep servo hold, and backlash/reversal handling, is documented in `docs/mini_dma_speed_control.md`
- position zeroing
- halt / stop support
- jog control refuses sub-step moves that would round to the current motor step
- Developer -> Benchmark Tic Transports compares native USB and `ticcmd` status/keepalive latency without moving the motor
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
- `raw_position_mm` is the commanded/confirmed Tic motor coordinate; `position_mm` and `strain_pct` are specimen displacement/strain and exclude configured backlash take-up travel during direction reversals
- physical remote tare and software tare are kept only in advanced hardware diagnostics because the normal workflow should leave the balance showing real grams

### Calibration Workflow

- The automatic `Calibration` recipe can be used with a stable non-transforming wire or, when needed, the installed microwire. Non-transforming wire is still better for pure backlash/stiffness checks because phase transitions do not add real material fluctuations.
- Motor step calibration is separate from the load/stiffness `Calibration` recipe. It uses raw Tic step increments rather than the current `steps/mm` value, so it can measure whether the right conversion is closer to the provisional `800 steps/mm`, the quick-gauge `1000 steps/mm` estimate, or some other value.
- Physical setup is still operator-controlled: mount and align the wire, confirm the zero-load scale reference, soft limits, max-load safety, and mandatory length setup settings, then start the recipe.
- The recipe uses separate preload-seek and micro-move settings. The preload seek can be faster/coarser for a bent or slack calibration wire; the forward/reverse micro-move step stays small for stiffness and backlash characterization.
- The default preload range is conservative for a short microwire. For a stiff 0.12 mm copper wire, raise the preload range and seek speed only as needed to make the wire visibly straight.
- The recipe records `calibration_baseline`, `calibration_preload`, `calibration_forward`, and `calibration_reverse` phases in the main CSV.
- Forward/reverse calibration records wait for a fresh scale sample after each micro-move, so 5 Hz request/response balances are allowed to catch up before stiffness/backlash points are stored.
- Completion computes a calibration report from the session points and stores it under `calibration.report` in the JSON metadata; interrupted calibration sessions with labeled calibration points write an `insufficient_data` report instead of leaving the report null. `copper_calibration` is retained as a legacy alias for old output readers.
- The report includes baseline load noise, forward and reverse load-path stiffness in `g/mm`, average stiffness, estimated backlash in `mm`, optional stress-strain modulus in MPa/GPa when length/diameter are available, and sample counts.
- A valid completed calibration applies the measured backlash to the live backlash setting, stores the calibration stiffness/noise as the current servo prior, and rescales that stiffness by `calibration_length / current_l0` when a later sample length differs. Backlash itself is treated as a rig parameter, not a sample-length-scaled value.

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
- if a current sweep reaches the voltage limit while measured current collapses near zero at a meaningful current setpoint, Mini DMA treats it as an open-circuit wire break, disables current, stops/saves the measurement, and asks whether to move displacement back to zero
- the mandatory length setup actively returns to the zero-load reference after the setup preload, and can accept the center of a stable near-zero balance plateau as the run's corrected zero reference, so a nominal `0 MPa` sweep remains an active zero-load/zero-stress measurement with strain still defined; the same plateau fallback is used during final current-sweep return/recovery so the motor does not keep relaxing forever when the balance has stopped changing near zero load, but high residual loads are not accepted as zero just because the ordinary stiffness/backlash tolerance band is wide
- stress-based recipe fields show their corresponding load values next to the controls, and load-based recipe fields show their corresponding stress values using the current diameter
- closed-loop target seeking samples feedback during each correction/settle/current step, but scheduled main CSV rows are throttled by the separate log interval; load/stress seeking now has two modes. Far from target, Mini DMA can keep the motor moving continuously and revise the predicted motor target on each fresh scale sample; near target, after target crossing, when the trend is suspicious, or when the scale sample has already been used, it falls back to the conservative one-move-at-a-time post-move feedback gate. In that gated mode, the recipe/dynamic speed is treated as desired average speed over the whole correction cycle, so Mini DMA can command a higher moving-part Tic speed to compensate for settle plus scale-response dead time, still clipped by the hard `mm/s` and `%/s` caps. Scheduled CSV rows defer when the logger is still waiting for required post-move force feedback, so a delayed scale sample does not abort a long current sweep. Current-sweep settle steps reacquire their load/stress/strain target before advancing to the next target, so a sweep that ends far from target keeps correcting instead of starting the next plateau immediately. Current-sweep balance corrections use calibrated or live load-path stiffness to predict correction distance, cap each move by specimen strain percentage instead of scale feedback cadence, and choose each speed from target error plus the measured load/stress trend under both a correction strain-rate ceiling and a hard `mm/s` stage-speed ceiling. The current ramp itself stays static during ordinary servo error, so operators can preserve thermal-history assumptions by choosing a lower fixed ramp rate instead of having Mini DMA pause or reshape the current ramp. Target seeking starts from an automatic `0.005 g` tolerance floor, raises it to the physical floor implied by motor step size and measured noise, rescales stored stiffness for the current gauge length, smooths speed as it approaches the target, and skips tiny reversals when backlash take-up would be larger than the predicted improvement. During setup preload target ramps, the configured `MPa/s` rate is converted through the stiffness estimate once the sample is responding, while slack take-up before a force response can use the configured setup stage-speed ceiling instead of creeping at a stale stiffness-limited speed.
- if a load/stress correction crosses the target but remains inside the physical reversal band implied by tolerance, motor step size, stiffness, and backlash, Mini DMA treats the target as reached instead of reversing immediately; this avoids near-target hunting in setup preload and iso-stress holds
- recipe completion stops the session log before running the return-to-start recovery popup; recipe stop/fault turns current annealing output off, keeps a resume point, and can ask whether to move displacement or load back toward zero without appending recovery samples to the recipe CSV; load-zero recovery can accept a stable near-zero raw-balance plateau, update the run's zero-load reference, and return to the first plateau position instead of walking indefinitely; paused recipes also turn current output off until resumed
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
- sample/project/output fields and dashboard plot selections are persisted as they change and when a session starts, so a crash or interrupted test window is less likely to wipe the operator's saved run identity

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
