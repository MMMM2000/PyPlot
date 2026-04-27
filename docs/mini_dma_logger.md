# Mini DMA Logger

This note is the handoff for the `Mini DMA Logger` workflow so development can continue on another machine without relying on Codex chat memory.

## Purpose

`Mini DMA Logger` is a hardware-driven stress/strain and heating workflow for a small stepper-based tensile rig. The immediate target is shape-memory microwire work, with a second planned use case for automated `Hsw` distribution measurements under load.

The measurement roadmap, copper-wire bring-up plan, and saved-recipe design notes live in `docs/mini_dma_measurement_plan.md`.

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

- one combined Mini DMA session for motion, load, and heating
- session naming helpers and run notes
- settings-panel spin boxes and drop-downs ignore mouse-wheel value changes so scrolling the panel cannot silently alter recipe or hardware options
- the settings panel disables horizontal scrolling, and note/log text wraps to the available width
- hardware driver details such as baud rates, serial request commands, `ticcmd`, device serials, and steps-per-mm are hidden by default under `Advanced hardware settings`
- an always-visible `EMERGENCY STOP` button in the dashboard header stops the active recipe/session, halts the Tic motor, and turns the supply output off
- recipe start runs a preflight that auto-detects/connects required scale and supply hardware before creating run files, and reports all missing devices together
- long recipe estimates switch from seconds to minutes/hours and the recipe panel includes a live progress bar
- numeric recipe summaries and spin boxes trim zero-only decimals, for example `20 g` instead of `20.0000 g`
- log messages appear in the `Run log`; the duplicate status-bar echo is hidden
- output to `TXT`, `CSV`, and `JSON` metadata sidecar
- shape-memory-friendly export columns for `Displacement`, positive applied tensile `Load`, `Strain`, and `Stress`

### Motion

- `ticcmd` integration for Pololu Tic status and commands
- automatic Tic path / serial detection for the connected controller
- stale saved `ticcmd` paths are ignored in favor of a discovered local Pololu installation
- motor moves energize the Tic, reset its command timeout, and exit safe-start before sending the position target; a short keepalive continues resetting the command timeout during active recipes/manual holds
- Tic status shows VIN motor-supply voltage and warns/preflights when VIN is below the motor-power threshold
- displacement recipes expose their own linear move speed, while iso-load, iso-stress, and iso-strain current sweeps are separate recipe choices with conservative correction step and correction move speed controls
- clear stacked `Move up` / `Move down` buttons can be held for continuous manual jogging from the last commanded target, with held movement advancing by the configured linear `Manual move speed` in `mm/s`
- current-sweep recipes expose `Tare scale at recipe/session start` inside the recipe settings, not hidden in specimen/logging settings
- position zeroing
- halt / stop support
- jog control refuses sub-step moves that would round to the current motor step
- displacement-driven automation recipes
- controlled current-sweep recipe that can hold load or stress while stepping current
- configurable soft position limits and max-load safety behavior; when the load limit is already exceeded, tension-increasing moves are blocked but relaxing moves remain available so the rig is not trapped above the limit

### Scale

- serial port enumeration and selection
- G&G-oriented serial settings
- automatic scale-port detection based on a live G&G serial response
- scale probe / diagnostics
- one normal `Tare scale` action for G&G-compatible balances via the documented remote tare command
- optional session-start scale tare before the first recorded point
- applied tensile load is displayed and logged as the positive tensile magnitude in `Load` / `load_g`, even when the balance reports negative values while lifting the weight; signed raw balance remains available as `raw_load_g` for diagnostics
- tensile displacement uses its own motion-direction setting, so the app can show/log positive `position_mm` while preserving raw Tic position as `raw_position_mm`
- software tare is kept only in advanced hardware diagnostics because it offsets Mini DMA without changing the physical scale display

### Heating / Current Annealing

- integrated heating subsystem in the same logger
- automatic supply-port detection for supported SCPI supplies
- live current / voltage / resistance / power channels
- recipe support for heating-inclusive workflows
- HMP4030 users can optionally assign CH1 or CH2 as the motor-supply channel; recipe preflight turns that channel on before checking Tic VIN, while current annealing remains on the configured annealing channel
- the main tabs are organized as `Recipe`, `Specimen`, and lower-priority `Hardware`, so scale/motor/power-supply setup does not dominate routine use
- iso-load, iso-stress, and iso-strain current sweeps own the current setpoints directly and are shown as separate recipe modes
- closed-loop target seeking logs feedback samples during each correction/settle/current step, adapts correction step and speed near the target, detects target overshoot, switches to fine reverse correction steps, and can apply a measured backlash take-up distance on direction reversals
- recipe stop/fault turns current annealing output off, keeps a resume point, and can ask whether to move displacement or load back toward zero; paused recipes also turn current output off until resumed
- behavior patterned after the existing current annealing logger rather than as a separate app

### Shape-Memory Workflow

- explicit gauge length `l0`
- preload-aware start logic
- mechanical zero is established only after a load threshold is reached
- pre-contact / straightening phase is handled before strain is treated as meaningful
- `.pydpj` import support for sample naming and diameter
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
- use preload-aware zeroing instead of blindly treating the initial stage position as strain zero
- keep graph setup configurable, but move the controls into a popup so the run dashboard stays clean
- make `Overview` collapsible because it is useful context but should not dominate the working layout
- support both shape-memory and Hsw workflows in one app, with recipes deciding behavior

## Current Blockers

### Scale Communication

The software side is prepared, but live scale communication is still blocked by the physical RS232 link.

Observed status during development:

- Windows detected the USB serial adapter successfully
- the balance appeared on `COM4` in one setup and `COM3` after a cable change
- the application could open the serial port
- the balance still returned no serial data during direct probes

Best current interpretation:

- the balance communication path still needs the correct RS232 null-modem / crossover wiring
- once the correct adapter or cable chain is in place, the code should already be ready to probe and read the scale

### Hardware Validation

The following are still pending live validation on the real rig:

- actual balance readings entering the logger
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
- preload-aware zeroing logic
- plot/dashboard visual review iterations

This means the code structure and main workflows were developed beyond pure scaffolding, but some features remain hardware-unverified until the scale link is working.

## What Was Verified On Real Hardware

The latest bench session confirmed that the app-level assumptions now match the actual rig wiring:

- Pololu Tic T500 reachable through `ticcmd` with serial `00501366`
- G&G balance replying on `COM6` at `9600` baud with `ESC+p`
- G&G remote tare working with `ESC+t`
- HAMEG `HMP4030` replying on `COM3` at `115200` baud
- HMP4030 channel `3` sourcing real current through the microwire path at about `15 mA`
- closed-loop motion against live scale feedback successfully moved the raw scale to roughly `-5 g`, reported/logged as about `+5 g` applied tensile load, after tare and then back near `0 g`

This means the main remaining risk is not basic communications anymore, but day-to-day measurement workflow polish and safeguards for real sample runs.

## Recommended First Steps On Another Machine

When resuming work elsewhere:

1. Check out the branch or merge commit that contains the Mini DMA work.
2. Open this file first.
3. Confirm the project `.venv` is healthy and use it for all Python commands.
4. Verify `ticcmd` is installed and reachable on that machine.
5. Use the built-in auto-detect actions to classify the Tic, scale, and supply ports if the COM numbering changed.
6. Reconnect the rig and test the motion side first with tiny jogs.
7. Re-test the scale link with the correct RS232 adapter/cable chain.
8. Use the built-in `Probe scale` action before trying full automated runs.
9. Once the scale is live, run one simple shape-memory measurement before tuning Hsw automation further.

## Recommended First Live Validation Sequence

After the correct scale adapter/cable is available:

1. Open `Mini DMA Logger`.
2. Keep `Pololu Tic Control Center` closed while Mini DMA is using the controller.
3. Use `Check Tic` and a tiny jog to confirm motion.
4. Use `Probe scale` and confirm live readings arrive from the balance.
5. Verify `Tare scale`.
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

After live scale communication is working, the natural next priorities are:

1. validate the full shape-memory workflow end-to-end on real hardware
2. tune strain/stress zeroing and safety thresholds from real runs
3. validate heating-inclusive runs
4. harden the Hsw automation loop against overshoot and noisy feedback
5. add later analysis or plotting helpers for plateau-based Hsw results if needed
