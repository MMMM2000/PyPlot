# Changelog

## 2026-07-03 06:30 UTC

- Restored the TMA settings wheel guard so mouse-wheel scrolling the settings panel scrolls the panel instead of changing focused spin-box or combo-box values.
- Changed TMA length setup so the fast Košice KERN/KCP setup-preload settle phase holds motor position and records the actual settled load/stress instead of continuing closed-loop preload corrections, while the Prague G&G profile keeps the previous continuous target-stability setup behavior.

## 2026-07-03 00:30 UTC

- Added high-speed KERN KCP scale support for the Kosice TMA bench, including 128000/256000 baud auto-detect, a 256000 baud `SI`/CRLF preset, and Prague G&G defaults kept at 9600 baud `ESC+p` / 250 ms.
- Made TMA hardware auto-connect faster and safer for the Kosice setup: sample names refresh from split wire tokens, scale probing avoids long serial scans, HMP4030/HMP4040 defaults bring up CH2 motor power before Tic VIN checks, and shared-HMP defaults use CH2 for the Tic motor rail plus CH3 for microwire current sweep.
- Updated KERN fast-scale control to be scale-profile aware, quantization-aware, response-based, and free of fixed MPa hold thresholds; KERN current-hold recovery now uses bounded drift recovery, earned resume, latest-sample lag clearing, setup-specific preload caps, and simulator calibration from the observed fast-feedback runs while leaving Prague-scale behavior unchanged.
- Added Kosice KERN full-run simulation and validation coverage using the observed approximately 16 Hz effective raw scale cadence, mounted-wire geometry, 0.01 g readability, controller wait overhead, and multi-seed adaptive-cap checks.
- Let bench plans and campaign manifests pin serial/HMP/Tic hardware settings, tolerate UTF-8 BOM markers, safe-off the configured HMP current and motor channels, and set Tic full steps/mm, step mode, winding-current limit, max speed, max acceleration, and max deceleration before unattended runs.
- TMA manual auto-connect, recipe preflight, and bench provisioning now apply and verify Tic step/current/motion settings; stopped recipes restore the configured idle motion limits after dynamic per-move speed caps.
- The shared HMP broker now enforces confirmed channel voltage/current limits, and TMA supplies planned current-sweep and motor-rail limits when it builds or auto-starts a shared-broker controller.
- Documented that TMA can set and auto-detect the KERN PC-side serial preset, while internal balance menu settings such as `prMode`, `triG`, `cont`, speed, zero, and stability remain manual unless a safe KERN remote-write path is verified.

## 2026-06-29 13:25 UTC

- Removed the TMA current-sweep mechanical load-loss stop condition so near-zero load during target acquisition no longer disables current or stops a recipe when electrical continuity/current feedback can still indicate an intact wire.
- The TMA logger now offers the sibling-run cleanup/archive review after normally completed recipes as well as after wire-break/contact-loss stops.

## 2026-06-26 12:31 UTC

- Renamed user-facing legacy logger wording to `TMA` across the launcher, logger, docs, reports, tests, and related help text while leaving legacy `mini_dma` module names, payload keys, and paths compatible.

## 2026-06-25 09:45 UTC

- TMA control-trace file write failures no longer stop active recipes; tracing is disabled and the recipe continues when the trace file handle fails.
- TMA control-worker crashes now finalize the run through the normal stop path so metadata stop reasons and summary images are still generated.

## 2026-06-24 13:00 UTC

- TMA runtime current-sweep edits now reject a refused current-limit refresh without stopping the active recipe, and shared-broker limit changes roll back when the broker refuses them.
- TMA displacement-to-zero recovery now stops after reaching the displacement target instead of adding a timed settle step.
- TMA progress text no longer offsets the progress bar fill, and microwire completion closes after selecting a wire.

## 2026-06-24 01:35 UTC

- Added software-only TMA real-run reference tools that scan existing run-quality artifacts and measurement CSVs into calibration tables, plots, and real-vs-simulation overlays for simulator scenario selection.
- Added real-run-inspired simulator scenarios, including a thin 8.3 um high-strain/high-hold stress ladder and a `realistic_run32_first_target` case with hidden free-strain roughness.
- Added p95 stress-error metrics, ranked policy-grid artifacts, adaptive-cap tightening, target-crossing-resume experiments, and a control-validation simulation suite for comparing candidate policies without changing live defaults.
- Split later-ramp simulator scoring from raw post-unwind slack reacquisition, and updated the live TMA current-hold path so larger adaptive hold corrections must be earned by observed same-side error improvement after fresh feedback.

## 2026-06-23 20:05 UTC

- TMA full-run simulation reports now expose hidden free transformation strain, motor-derived measured strain, elastic mismatch, and measured-vs-free strain tracking error so controller changes can be judged against modeled wire contraction/elongation.
- Added broad free-strain, stress-ladder, and control-policy matrices covering good high-strain wires, early/delayed 19/8 behavior, bad Co6-style wires, weak/noisy wires, rough transformations, delayed feedback, stiffness variants, and multi-target 0 -> 50 -> 100 MPa ladders.
- Added target-scaled quality status, normalized stress-error/tracking metrics, current-phase event counts, target-lead/correction-cap policy comparisons, and stable per-policy seeds to the simulator reports.
- Increased the production iso-current stress-ramp per-command cap from 0.08% to 0.12% strain of gauge length based on the stable simulator grid, while leaving adaptive cap growth disabled by default.

## 2026-06-23 18:25 UTC

- TMA full-run software simulations now include a bad `Ni47Fe24Ga23Co6 2/1` first-overheating scenario with early transformation stress surge, delayed scale feedback, bounded correction, low usable strain, and raw stress break/contact-loss behavior for control-policy screening.
- The good-wire and bad-wire full-run simulations now start from unloaded target acquisition and ramp the active target to 50 MPa before current sweep; plotted strain remains derived only from simulated controller motor motion and gauge length.

## 2026-06-23 15:45 UTC

- Let TMA iso-stress current sweeps update the independent first-overheating max current during the active preheat ramp, with rejection when the ramp has already passed the safe update point.

## 2026-06-23 14:05 UTC

- Added a software-only TMA full-run simulator for first-overheating style target acquisition, current rise, endpoint recovery, reverse unwind, bounded mechanical corrections, delayed scale feedback, and slack take-up.
- Added calibrated realistic 50 MPa good-wire simulation, full-run scenario reports, parameter sweeps, and machine-readable artifacts; strain-current plots are now derived from simulated controller motor motion rather than artificial current/transformation strain shaping.
- Recalibrated the realistic full-run transformation driver against real 50 MPa run34 hold spans and bounded processed-noise admission so broad raw stress envelopes cannot hide a materially off-target processed center.

## 2026-06-23 13:52 UTC

- Tightened TMA current-hold processed-signal bands so large raw fluctuations centered near target do not cause chasing, while biased processed centers still trigger recovery.
- Disabled cruise feedback for TMA current-sweep load/stress control, added endpoint recovery checks before current sweep or unwind steps can complete, and kept grouped strain/current core plots from dropping first-overheating rows that do not have a numeric plateau index.
- Expanded the TMA wire simulator with processed-center scenario-matrix reports covering noise, transformation, slack, stiffness, wire diameter, and delayed-feedback cases.

## 2026-06-23 13:45 UTC

- TMA recipe progress text now uses the normal application font instead of a fixed-width system font.

## 2026-06-23 10:38 UTC

- Added a deterministic TMA virtual wire simulator and CLI for generating synthetic measurement/control traces for current-sweep controller tests.
- Documented built-in robust-center, safety-rail, transformation-bias, reversal, and wire-break scenarios.

## 2026-06-19 07:20 UTC

- Record a final TMA measurement point when a normally completed very short recipe would otherwise save a header-only measurement file.

## 2026-06-19 00:10 UTC

- Included the AC Susceptibility Logger in the packaged launcher experiment-process hidden imports so frozen colleague builds can start all primary logger apps.

## 2026-06-19 00:00 UTC

- Made PyPlot automation session discovery use an explicit or environment-based registry path so source runs, tests, and child processes can reliably find the same live session.

## 2026-06-18 18:25 UTC

- Hardened the shared HMP broker so active channel leases cannot be overwritten by role or profile changes, and so same-owner reconnects reuse the existing lease instead of replacing it.
- Added AC susceptibility to the shared-HMP setup role list and capped HMP-backed AC voltage limits to the physical HMP range.
- Made TMA shared-broker disconnect explicitly switch leased current and motor channels off before releasing them.
- Kept Iso-stress fatigue recipes on the expected up-and-back current sweep even if a stale hidden one-way optimization setting is present.
- Preserved the Current Annealing shared-broker port setting when reopening the app and removed duplicate UI signal connections found during release review.

## 2026-06-18 15:00 UTC

- Moved Current Annealing recipe controls into a Recipe tab and advanced broker/HMP controls into a Hardware tab while keeping recipe settings editable before hardware connection.
- Added a Current Annealing plot configuration dialog for the live dashboard, including bottom/left/top/right axis choices for both plots.
- Current Annealing Replace now moves the previous output file and metadata sidecar to Trash or a safe replacement backup before writing the new run.
- Current Annealing now reports hardware auto-connect failures immediately and uses `A/mm²` in visible current-density labels.
- Moved voltage-limit behavior into the Hardware tab and made current sweeps always reverse to zero after reaching the configured maximum current.
- Added an Update running recipe control so automatic Current Annealing runs can apply safe mid-run edits to max current, start current, ramp rate, and loop settings.

## 2026-06-18 12:30 UTC

- Current Annealing live/history plots now share the same cycle color logic and ignore one-point current-direction jitter during cooling/heating.
- Current Annealing measurement history now uses a scrollable stacked view instead of tabs and keeps more recent runs.

## 2026-06-18 12:00 UTC

- Current Annealing now warns/stops on zero measured current in shared-HMP broker mode instead of silently waiting at startup with an open contact.
- Current Annealing now refreshes shared-broker channel limits before a run and blocks starts where the requested current exceeds the confirmed broker limit, avoiding silent current clamping.

## 2026-06-18 09:55 UTC

- Contain TMA current-hold adaptive recovery to one motor Tic during high-error volatile/unstable stress excursions, while preserving larger adaptive corrections for quiet monotonic recovery.

## 2026-06-18 08:20 UTC

- Added fluctuation-gated delayed-feedback waiting for TMA current-hold recovery so quiet samples keep fast adaptive corrections while bursty stiff-wire responses wait for extra scale samples before compounding another motor move.

## 2026-06-18 00:00 UTC

- TMA now writes post-run summary images for remote review: a phone-friendly `run_summary.png`, a diagnostic `run_summary_detail.png`, and `run_summary.json` in each run folder.
- The TMA summary plots show stress/load legends, sample diameter and initial length, resistance instead of voltage on electrical diagnostics, temperature when available, and hide obvious wire-break tail points from curve scaling while preserving the stop reason.

## 2026-06-17 15:23 UTC

- Let TMA current-hold recovery cautiously grow above one motor Tic when an unstable hold is still showing persistent same-sign improvement, while keeping one-Tic damping for reversals and worsening responses.
- Treat same-sign current-hold stress drift away from target as bounded dynamic recovery instead of oscillation, record filtered slope/noise in the control trace, and show active current-sweep progress from the current fraction rather than an exhausted nominal tick count.
- Add an optional bench-plan current-hold quality watchdog for optimization runs so clearly bad candidates can stop early with explicit stop metadata.
- Honor the TMA current-sweep `reverse_current` recipe flag so optimization recipes can run a one-way current ramp while keeping the default sweep-back behavior and voltage-limit unwind safety.
- Add a reusable TMA stiff-sample guard CLI that regenerates offline evidence for stiffness-scaled current-hold drift recovery and historical oscillation clamps before stiff-wire hardware validation.

## 2026-06-17 09:55 UTC

- Added a TMA hardware option to disable the optional IR camera/thermometer so hardware auto-connect skips it and temperature fields stay blank.
- Collapsed first-overheating child settings when the first-overheating sweep is disabled.

## 2026-06-17 09:15 UTC

- TMA current-sweep corrections no longer stop a recipe just because accumulated closed-loop correction travel exceeds the legacy hidden travel counter; direct load, stress, wire/contact, current, voltage, and per-step correction caps remain active.

## 2026-06-17 00:00 UTC

- Added a TMA `Iso-stress fatigue` recipe for repeated fixed-stress current up/down cycles, with optional first-overheating preheat support, finite cycle count, descriptive recipe filenames, saved JSON/settings support, and cycle-numbered recipe steps for later drift/wire-break analysis.
- Added an offline `mini_dma_fatigue_learning.py` report tool that groups saved repeated iso-stress runs, excludes non-comparable or too-short data, estimates transformation-current shifts from resistance/strain slopes, and emits review-only JSON/CSV/Markdown priors for future fatigue measurements.

## 2026-06-16 21:35 UTC

- Reorganized the AC susceptibility logger setup panel into clearer output, hardware, LCR, measurement-plan, and run-status sections with passive output-path and shared-broker lease status text.
- Added clearer shared HMP broker diagnostics for unreachable brokers, refused leases, stale leases, direct-serial access denial, wrong channel/profile, and stale channel-limit failures.
- Made TMA shared-broker control retry once after stale lease errors and report broker connection failures with operator-facing diagnostics.
- Added cached TMA Builder project sample suggestions for faster sample naming/autofill refreshes during background project imports.
- Expanded TMA run-quality and core-plot summaries with stop classification, metadata warnings, current-hold recovery windows, voltage/current compliance events, and richer plot annotations.
- Made TMA trace replay tolerate missing/invalid metadata or trace files and report warnings instead of failing before analysis.
- Stopped TMA current-sweep voltage-limit unwinds immediately when the supply indicates open circuit or wire contact loss, before any mechanical recovery seek is attempted.
- Scoped TMA predictive seek control to active controlled phases while still honoring explicit calibrated or live stiffness for ordinary load/stress seeks.
- Made TMA IR thread disconnect/close cleanup tolerate naturally finished Qt threads that have already been deleted.
- Made TMA saved Builder project import cancellation clear pending retry state and tolerate already-deleted Qt thread wrappers.
- Cleaned up Current Annealing fabrication-folder background-load completion so the UI resets promptly when the worker has already finished.
- Added TMA elastocaloric recipe JSON round-trip coverage and offscreen UI screenshot evidence for the fast strain-jump workflow.
- Made AC run failures show run-status sidecar/fallback details in the status label and warning dialog, including rows written and local fallback status path when the primary output path disappears.
- Made AC completed/stopped sweep messages show run-status sidecar details, including rows written and the status-file path.
- Made the AC output section show the planned run-status sidecar and local fallback paths before a sweep starts.
- Routed AC shared-HMP broker current-source failures through the shared operator-facing broker diagnostics.
- Reused fresh TMA Builder project cache entries for immediate sample/microwire suggestions while background exact-match imports continue.
- Added a TMA run-quality CLI option to generate the standard core PNG/JSON plot artifacts while writing `run_quality.json`.
- Made TMA batch core-plot generation continue past incomplete run folders while reporting per-run plot errors.
- Kept AC sweep-start failures from leaving the UI stuck in a running state when PSU preparation or worker startup fails.
- Avoided redundant TMA Builder project auto-imports and trusted-diameter flicker for condition-only sample-name edits.
- Reset AC baseline UI state if empty-coil baseline worker startup fails after the run has been marked active.
- Added cached power-supply setpoint/readback columns to TMA control traces for easier electrical/mechanical fault forensics.
- Added an AC susceptibility UI redesign note that separates safe stabilization work from the larger post-bench-test refactor.

## 2026-06-16 14:05 UTC

- Made TMA current-hold recovery scale cautiously above one motor Tic after same-sign corrections measurably improve load/stress error, while immediately damping back to one Tic when recovery worsens or becomes unstable.
- Added control-trace reasons and control-logic metadata for adaptive current-hold recovery decisions.

## 2026-06-16 13:21 UTC

- Let TMA current-sweep target ramps use their phase-local stiffness estimate once learned, while keeping conservative stiffness damping for current-hold recovery.

## 2026-06-16 12:58 UTC

- Refresh shared HMP broker channel limits even when TMA already owns a confirmed CH4 role.

## 2026-06-16 12:31 UTC

- Include independent TMA first-overheating current maxima when checking the shared HMP CH4 recipe current limit.

## 2026-06-16 12:13 UTC

- Dampen TMA iso-stress current-hold corrections to single motor steps when the load/stress response becomes unstable or repeatedly overshoots the target, and only accept learned current-hold stiffness updates when they shrink future correction steps.

## 2026-06-16 11:58 UTC

- Hide the TMA first-overheating "First max" current row whenever it is using the normal current-sweep max current.

## 2026-06-16 11:44 UTC

- Let TMA recipe and manual hardware preflight auto-start the local shared HMP broker when the shared-broker endpoint is down, including automatic HMP COM-port selection for broker startup.

## 2026-06-16 11:35 UTC

- Improved TMA Sample-tab responsiveness by coalescing settings saves and delaying automatic fabrication workbook reads after sample-name edits.
- Added an optional independent first-overheating maximum current for iso-load, iso-stress, and iso-strain current-sweep recipes while keeping the current ramp speed shared with the normal sweep.

## 2026-06-16 09:10 UTC

- Hide the legacy TMA open-loop displacement/Hsw recipe types from the recipe dropdown.
- Add a TMA `Elastocaloric effect` recipe that reuses the iso-current transition, waits for temperature stabilization, then applies and releases a configured strain in single fast steps for thermal-camera transformation measurements.

## 2026-06-16 00:00 UTC

- Hardened AC susceptibility long runs against disappearing output drives by mirroring run status to a local Downloads fallback and surfacing output write failures as stopped/failed runs instead of stale running UI.

## 2026-06-15 15:30 UTC

- Added a TMA `Iso-current stress ramp` recipe that reuses the low-stress current transition flow and ramps stress up/down at a configured `MPa/s` rate for each current.
- Saved and restored the new stress-ramp settings in recipe files and logger settings.

## 2026-06-15 14:20 UTC

- Improved TMA `Iso-current stress ramp` tracking by allowing larger bounded stress-ramp corrections and adding feed-forward motion while the measured stress lags the moving `MPa/s` target.

## 2026-06-15 13:02 UTC

- Improved TMA MLX90640 Cube raw status reporting when the selected camera port is silent, running thermometer firmware, or streaming bytes that are not valid `MLXE`/`MLXR` camera packets.

## 2026-06-15 10:30 UTC

- Rename the TMA constant-current stress-strain recipe UI to iso-current stress-strain.
- Add an iso-current current-transition ramp that holds a low stress target while ramping to each current level before the stress-strain scan.
- Rework the iso-current recipe page into target, mechanical scan, current level, and collapsible current-transition sections with current-density/load equivalents.
- Always scan iso-current legs up and back to the start target, and default fixed-step motion to 0.2 mm/s while still waiting for fresh scale feedback.
- Delay fixed-step stress-strain logging until fresh post-move feedback is available, reducing stale-feedback strain jumps.

## 2026-06-15 02:15 UTC

- Added AC susceptibility sweep run-status sidecars with heartbeat/checkpoint metadata, final stop status, shared-HMP lease context, and resume warnings for stale or unclean previous runs.

## 2026-06-13 13:20 UTC

- Polished the AC Susceptibility Logger setup layout so hardware status wraps cleanly, run controls are always visible in the experiment plan, inherited duplicate controls are hidden, and AC progress appears in one predictable place.

## 2026-06-12 18:10 UTC

- Added in-app NUCLEO-H753ZI wiring guidance for the MLX90614 spot thermometer
  and MLX90640 thermal camera modules in both the Thermal Camera Viewer and
  TMA Logger.
- Enabled TMA Logger help and added an explicit IR sensor selector for
  MLX90614 spot-thermometer firmware or MLX90640 Cube raw camera firmware.
- Added a TMA IR-panel Live camera button that opens a passive embedded
  MLX90640 heatmap popup fed by the active TMA IR stream, so it can be used
  during a measurement without taking over the serial port.
- Added a TMA IR-panel Flash firmware button that builds and flashes the
  selected MLX90614 or MLX90640 STM32Cube firmware with STM32CubeProgrammer.
- Added MLX90640 Cube raw parsing to TMA IR logging, with live max/mean/min,
  center, hotspot, ambient, age, and rate summaries when calibrated Celsius frames
  are received.

## 2026-06-12 14:45 UTC

- Fixed TMA MLX90640 Cube camera reconnects so pending camera packets are not discarded before calibration/frame parsing, and prevented duplicate IR reader startup when IR logging is already connected.

## 2026-06-12 14:31 UTC

- Added Current Annealing Logger metadata autocomplete trust handling for exact composition and microwire diameter matches, including red/green diameter indication.
- Aligned the Current Annealing current-density plot axis with the current-axis tick positions.

## 2026-06-12 12:44 UTC

- Made the TMA thermal-camera popup behave as a normal minimizable window on Windows, with a narrower layout and a 1 fps display option.
- Added IR camera/thermometer connection to TMA manual hardware auto-connect when an IR port is selected.
- Simplified dashboard temperature plotting to a single Temperature (C) channel backed by coalesced IR values while preserving full-rate IR sidecar logging.
- Closed TMA recipe sessions on control-stop recovery paths and snapshot-locked live buffers to prevent post-stop time-plot tails and deque mutation errors during refresh.

## 2026-06-12 11:55 UTC

- Added TMA embedded thermal-camera view pause/resume and display-rate controls.
- Stopped recipe session temperature, telemetry, and live plot sampling while recipes are paused or stopped.
- Refreshed the current-sweep supply current limit before recipe current updates to guard stale CH4 limits after runtime changes.

## 2026-06-12 08:33 UTC

- Moved TMA recipe control ticks off the Qt UI timer so long measurements keep controlling even when the dashboard is visually busy.
- Batched live run-log updates and coalesced worker-triggered progress/label refreshes to reduce UI stutter during active TMA runs.
- Kept live average-speed sampling independent from recipe timing so the speed display can update without disturbing control-loop timing.

## 2026-06-11 14:42 UTC

- Let TMA passively log MLX90640 text-frame thermal camera streams by recording frame max/min/mean/center and hotspot coordinates in the IR sidecar while using frame max as the plotted apparent temperature.

## 2026-06-11 14:15 UTC

- TMA current-hold recovery now downgrades correction trust after repeated overshoot or worsening stress/load response, shrinking subsequent correction caps until the response stabilizes.

## 2026-06-11 09:15 UTC

- Changed TMA standard motor move log entries to lead with signed commanded micrometers and tension/relax direction, while keeping target position and Tic-unit count for diagnostics.

## 2026-06-11 08:37 UTC

- TMA Logger now opens loaded microwire suggestions when the microwire field receives focus or is clicked, without rebuilding suggestion data.
- TMA Builder-project diameter imports now retry automatically after sample fields change during an active background import, so valid microwire diameter updates are not dropped until another sample switch.

## 2026-06-10 13:35 UTC

- TMA replacing an existing run now moves the old output to Trash/Recycling Bin when possible, with a timestamped preserved-folder fallback instead of silently overwriting the previous measurement.
- TMA runtime recipe updates now preserve the active plateau's paired reverse current sweep before moving to the next stress target.

## 2026-06-10 11:23 UTC

- Show missing or stale TMA scale readings explicitly in the dashboard load/stress header instead of displaying misleading zero values.

## 2026-06-10 11:15 UTC

- Allow TMA current-sweep runtime recipe updates to apply hold and ramp settings to the active sweep step when safe.

## 2026-06-10 10:14 UTC

- Refresh the shared-HMP TMA CH4 current limit from the active recipe maximum before preflight and current-channel preparation, preventing long sweeps from stopping when the broker still has an older lower limit.

## 2026-06-10 09:20 UTC

- Added an opt-in TMA wire-break cleanup review that groups sibling runs by metadata-derived sample and recipe mode, then lets operators archive selected older run folders without deleting data.

## 2026-06-10 08:17 UTC

- Mark TMA wire diameter as stale immediately when the sample naming fields change, and show a green imported state only after the current sample's diameter has actually been imported.

## 2026-06-09 15:02 UTC

- Verify TMA current-sweep channel output after recipe current commands and retry output enable once when CH4 readback reports OFF.

## 2026-06-09 14:48 UTC

- Move TMA recipe step dispatch behind a dedicated automation controller boundary so the Qt window no longer owns the active control-loop dispatcher directly.

## 2026-06-09 09:17 UTC

- Fixed TMA sample headers so stale auto-generated sample names from a previous wire are replaced when the Sample tab composition/wire fields identify a new sample.
- Smoothed TMA current-sweep target acquisition by switching to small probing corrections after a load/stress reversal, while preserving fast stage-speed moves for large monotonic target errors.

## 2026-06-05 15:45 UTC

- Added operator-guided LCR open/short fixture correction controls to the AC Susceptibility Logger.
- Recorded LCR correction state in AC baseline and sweep metadata so runs show whether open/short correction was enabled.
- Added an offline AC empty-coil subtraction tool that writes a derived TSV with baseline-subtracted LCR columns while preserving raw measured columns.
- Kept AC auto-connect from resetting selected LCR frequencies/amplitudes and added an in-app busy progress state while open/short correction runs on the meter.

## 2026-06-05 15:30 UTC

- Current Annealing can now load optional `.pydpj` projects and fabrication spreadsheet folders to suggest composition, microwire, and diameter values.
- Current Annealing displays current-density equivalents beside current values and on the top axis of the live resistance-vs-current graph when a diameter is known; the logger hides density values when no diameter is available.
- Fabrication spreadsheet loading now runs in the background so massive folders do not freeze the Current Annealing UI.
- The Current Annealing Microwire field now displays slash-style labels such as `1/2`; generated filenames still use filesystem-safe separators.
- Current Annealing process settings now give current-density readouts their own row space and pin the progress/time estimate strip directly above the run buttons.

## 2026-06-05 15:15 UTC

- Hid the inherited Current Annealing progress bar in the AC susceptibility logger so only the AC run progress is shown.
- Made AC sweep start try the normal hardware auto-connect path before warning that the LCR meter is not connected.

## 2026-06-05 15:08 UTC

- Current Annealing plots now ignore invalid zero-resistance readbacks, including at the configured start current.
- Current Annealing plot colors keep increasing-current segments on the warm palette and decreasing-current segments on the cool palette.
- Current Annealing live plots now keep direction-change turning points visible in both adjacent color runs instead of letting the cooling trace steal the final heating point.

## 2026-06-05 14:55 UTC

- Fixed AC susceptibility shared-HMP broker mode so auto-connect and sweep start validate or auto-start the broker on the selected AC channel instead of failing with a refused socket when no broker is already running.

## 2026-06-05 12:17 UTC

- Keep Current Annealing pyqtgraph plots boxed with quiet top/right axes.
- Let Current Annealing broker connect auto-detect the HMP port while avoiding raw serial-device probe noise and only starting a broker from the detected or selected HMP.

## 2026-06-05 11:35 UTC

- Forward-ported the Current Annealing pyqtgraph dashboard and expanded shared-HMP broker hardware panel into the TMA integration branch.
- Added HMP current-floor normalization used by shared-broker/current-annealing setpoint handling.

## 2026-06-04 14:55 UTC

- Added a TMA mounted-wire campaign manifest for the Ni50Fe27Ga23 12/2 heat-shield 33.68 mm, 80 mA optimization run, with explicit operator confirmations for mounted sample identity, length, CH3 motor rail, CH4 current path, and broker lease state before live execution.
- TMA shared-HMP auto-connect now applies the current bench CH3/CH4 defaults when entering Tic motor-power preflight and can confirm unused broker CH3/CH4 roles before leasing them, without taking over conflicting broker roles.
- Added reusable 0.6, 0.4, and 0.3 mA/s TMA iso-stress comparison recipes and recorded the campaign run folders used for the 0.8 mA/s baseline, rejected fast-recovery experiment, 0.6 mA/s repeat, selected 0.4 mA/s repeats, and rejected 0.3 mA/s boundary probe.
- Recorded rejected late-stage TMA optimization experiments for adaptive hold gain, current-hold slope lookahead, and 1.0 s hold confirmation; the selected 0.4 mA/s baseline logic remains the best time/precision tradeoff from the campaign evidence.

## 2026-06-04 12:39 UTC

- Added master/worker coordination guidance, reusable worker-ledger and TMA campaign templates, and a campaign manifest checker for pre-hardware validation.

## 2026-06-04 00:00 UTC

- Added opt-in AC Susceptibility Logger support for the shared HMP broker, including channel leases, broker readback, and channel-only shutdown/release on stop or error.
- Added a bounded continuous LCR debug JSONL sidecar for transition/cadence diagnosis, with persisted cadence and row-cap settings.
- Kept AC HMP4030/HMP4040 direct and shared-broker current sweeps at the configured voltage limit while changing only current setpoints, matching the other HMP logger integrations.
- Reasserted shared-broker output-on during each AC current step so an active sweep does not rely on stale HMP output state.
- Reworked AC frequency/excitation selection into compact checkable preset chips with custom list editing still available, and capped the settings column width so plots keep more room.
- Documented shared-broker operation, debug-stream metadata, and the live bench plan for MED/SLOW/AVG tuning.

## 2026-06-03 10:35 UTC

- Added `.pydpj` sample-geometry lookup to the AC susceptibility analysis CLI so report runs can use Data Builder microscope diameters instead of hand-entered values.
- Updated the 12/2 AC susceptibility handoff to use the project-file diameter for `Ni50Fe27Ga23 12/2`.

## 2026-06-03 09:27 UTC

- Improved TMA imports so selected run folders, parent folders containing multiple run folders, and multiple folder selections load without requiring a separate placeholder import file.
- Changed TMA power top axes to default to length-normalized `Power/cm [mW/cm]` when initial-length metadata is available, with absolute `Power [mW]` still available from the TMA plot settings.
- Marked TMA first-overheating preheat sweeps as separate dashed diamond traces with compact `1st:` legend labels when run metadata identifies the first-overheating target.

## 2026-06-03 08:30 UTC

- Clarified AC susceptibility relative-change outputs with explicit martensite-window, austenite-window, `chi_A/chi_M`, and percent-drop columns.
- Exported the raw `(L_wire - L_empty) / L_empty` relative inductance change alongside filling-factor-corrected apparent susceptibility.
- Added a generated susceptibility equation audit report explaining the filling-factor correction and relative-change denominators.

## 2026-06-02 18:35 UTC

- Added an electrical live shared-HMP validation CLI that coordinates Current Annealing and TMA current-sweep broker clients, records readback artifacts, classifies voltage-limited/open-circuit current paths, checks channel isolation guardrails, and leaves CH1/CH4 safe-off while preserving the TMA motor rail state.

## 2026-06-02 16:15 UTC

- Set the default TMA iso-stress current-sweep ramp rate to 0.8 mA/s based on the 12/2 live optimization results, and treat 0.8 mA/s as the baseline in the optimization campaign template.

## 2026-06-02 15:15 UTC

- Made TMA bench automation reapply explicit plan sample identity immediately before run start, so saved Builder/project autofill cannot overwrite a campaign diameter.
- Added a reusable TMA per-run core plot command for phone-readable stress-time and strain-current artifacts with current-hold highlighting and stress-error metrics.
- Cleared child-owned TMA bench locks from the supervisor after finished-metadata recovery, so completed supervised runs do not leave stale lock files.

## 2026-06-02 14:45 UTC

- Stabilized TMA fabrication composition and microwire autofill so completer selections keep the popup/model alive, reuse cached suggestion models, and apply diameters without laggy rebuilds during rapid sample changes.

## 2026-06-02 12:50 UTC

- Avoid drawing duplicate TMA dashboard curves when the secondary Y axis is only an equivalent unit conversion, while keeping the secondary axis labels and scale visible.

## 2026-06-02 12:38 UTC

- Added an all-condition delta chi' current-sweep grid for AC susceptibility report diagnostics.

## 2026-06-02 12:15 UTC

- TMA voltage-limit current unwind now waits for the mechanical target to recover before advancing to the next stress/load ramp, preventing a post-unwind zero-load state from immediately tripping the mechanical load-loss guard.

## 2026-06-02 12:05 UTC

- Added DC wire-resistance-vs-current panels next to AC susceptibility report curves.
- Included DC wire resistance columns in the Origin-ready chi' export table.

## 2026-06-02 09:15 UTC

- Made PyPlot close teardown avoid recursive menu/dialog child scans that could crash long Windows GUI test runs.

## 2026-06-02 08:30 UTC

- Added a repeatable TMA optimization campaign template, preflight checker, and standard report generator scaffold.
- Documented the campaign workflow so optimization runs start from an approved control source and produce consistent stress/time plus strain/current reports with current-hold highlighting.

## 2026-06-02 04:40 UTC

- TMA bench supervisor now prints ASCII-safe JSON summaries on Windows consoles while still writing full status files.

## 2026-06-01 21:34 UTC

- Added a supervised TMA bench-plan launcher that records child PID/status/log paths and turns the current-sweep HMP channel off when the child exits.

## 2026-06-01 21:17 UTC

- TMA current-sweep sessions now record `correction_travel_limit` as a specific fault stop reason with travel-limit detail in session metadata.

## 2026-06-01 17:10 UTC

- Made shared HMP broker clients use a longer configurable request timeout so dual logger runs are less likely to abort while queued PSU requests are being served.
- Made Current Annealing shared-broker measurements retry one transient missing readback before treating the PSU as unresponsive during concurrent broker use.

## 2026-06-01 15:34 UTC

- TMA current-sweep runtime updates now show the Update remaining sweeps button only when visible recipe edits would change the active or remaining current-sweep plan.
- Edited-but-not-applied current-sweep fields are subtly highlighted until the runtime update is applied.

## 2026-06-01 14:41 UTC

- TMA equivalent-unit labels now preserve significant trailing zeros for integer values such as 800, 750, and 80.
- TMA project sample import now recognizes `Imax (mA)` rows as current-limit values, and microwire field edits report bad project or fabrication data without crashing.
- TMA dashboard speed now reports effective average linear motion in `um/s`, with commanded speed retained as secondary context.
- TMA current-sweep advanced speed/cap controls now live in a Settings menu dialog instead of the inline recipe panel.

## 2026-06-01 14:39 UTC

- TMA experiment child processes now write stdout, stderr, and Python faulthandler output to ignored logs under `logs/experiment_processes/`.
- TMA saved Builder project auto-import now runs in the background during startup so a large saved `.pydpj` cannot freeze the initial UI.
- TMA setup plots reserve right-axis space to avoid clipping the load axis in the length setup dialog.
- TMA task summaries now prefer the active long-running recipe step so stress target ramps do not flicker to the next step.
- TMA mid-run current-sweep updates now extend the active current ramp when the edited end current is still safely ahead of the live setpoint, while reporting conservative future-only updates when it is not.

## 2026-06-01 12:35 UTC

- TMA current-sweep recipe inputs now use narrower fixed widths so labels and equivalent-unit text stay readable in the normal recipe column.
- TMA sample-name auto-import now reports project/fabrication lookup failures without crashing while editing composition or microwire fields.

## 2026-06-01 11:35 UTC

- TMA current-sweep recipes can now apply visible current-sweep edits to remaining, not-yet-started sweeps while leaving the active sweep frozen and logging the runtime override.
- Runtime updates can re-plan future iso-load, iso-stress, or iso-strain target plateaus when target start/end/step changes are made mid-run.
- Current-sweep fields that cannot safely modify the active recipe are shown in a gray read-only state during a run, while runtime-editable fields remain normal.

## 2026-06-01 10:55 UTC

- TMA length setup now commits the run-specific zero-load scale reference when applying the setup L0 baseline, preventing the recipe from starting with residual stress after setup.

## 2026-06-01 10:30 UTC

- Made TMA Logger startup width adjustments avoid fragile internal Qt child widgets, improving startup stability in long GUI sessions.
- Guarded PyPlot subwindow state handling against non-state-change Qt events that can arrive during window close in full-suite runs.

## 2026-06-01 10:05 UTC

- TMA current-sweep recipe fields now use stable widths, show current-density ramp rate, use a 0.2 mA/s current-ramp step, and format current-density equivalents with compact precision.

## 2026-06-01 09:45 UTC

- Corrected TMA Builder strain summaries so each stress/load row reports the strain measured at the maximum current point after per-curve l0 recalculation.

## 2026-06-01 09:35 UTC

- TMA length setup plots now draw setup samples in elapsed-time order and keep displacement markers, avoiding backward line segments when setup samples arrive out of order.

## 2026-06-01 09:10 UTC

- TMA voltage-limit current unwind now obeys the current-hold recovery logic, so a transforming wire that loses load during current decrease holds current and pulls back to target instead of continuing to unwind to the start current.

## 2026-06-01 08:30 UTC

- TMA length setup displacement plots now draw the displacement trace without per-sample markers, reducing visual duplicate-dot clutter while retaining the recorded setup samples.

## 2026-05-29 22:16 UTC

- Ensured the Microwire EDA window closes progress dialogs and waits for its worker thread during teardown.

## 2026-05-29 21:50 UTC

- Stopped legacy Data Logger refresh timers when the window closes so headless test runs and repeated logger sessions can exit cleanly.
- Sanitized legacy Data Logger output filenames as well as subfolder names on Windows.

## 2026-05-29 21:38 UTC

- Skipped no-op TMA displacement recovery when a completed recipe is already at the return target, avoiding unnecessary hardware preflight and keeping headless verification from hanging.

## 2026-05-29 21:15 UTC

- Updated PyPlot automation docs to describe the full Microwire Builder recipe scope, including copy-safe project refreshes, supported graph-backed sections, Assemble rebuilds, and latest-database archiving.

## 2026-05-29 19:31 UTC

- Kept PyPlot legends visible in Microwire Data Builder current-annealing preview thumbnails.

## 2026-05-29 19:14 UTC

- Prevented Microwire Data Builder startup auto-open from re-entering project loading while a project is already being loaded.

## 2026-05-29 18:32 UTC

- Preserved TMA current-density and l0 axis-label context when exporting shared PyPlot graphs to Origin, and covered line+symbol marker export behavior.

## 2026-05-29 18:18 UTC

- Renamed public Builder graph columns for the Manual stress/strain section while keeping legacy Shape memory stress/strain project and Word-report columns readable.

## 2026-05-29 18:04 UTC

- Added headless Microwire Data Builder automation coverage for DMA iso-stress, Manual stress/strain, and FMR section updates.
- Verified these graph sections can update a copied `.pydpj`, persist embedded record payloads, skip invalid inputs, and rebuild Assemble rows from automation recipes.

## 2026-05-29 17:48 UTC

- Added headless Microwire Data Builder automation coverage for updating VSM hysteresis records and rebuilding Assemble from a copied `.pydpj`.
- Direct-file Builder imports now use the file stem as the sample label before falling back to the parent folder, so automation recipes that pass individual files do not label rows with temporary working-folder names.

## 2026-05-29 17:32 UTC

- Added headless Microwire Data Builder automation coverage for updating the Current annealing section and rebuilding Assemble from the copied `.pydpj`.
- Current annealing section automation now skips parsed numeric files that do not contain a recognizable composition/draw/piece identity, so manifests report them as skipped instead of counting them as updated records that later disappear.

## 2026-05-29 17:12 UTC

- Reduced false positive As/Af/Ms/Mf estimates by rejecting tangent-transition fits with too little slope contrast or an unrealistically narrow transition window.
- Preserved TMA current-sweep return-leg points when they match earlier heating-leg values, so cooling-side transition-current estimates are not lost during duplicate cleanup.

## 2026-05-29 16:48 UTC

- Kept Microwire Data Builder suppressed/headless project loads from creating modal progress dialogs, reducing startup and automation event-loop fragility while preserving the GUI progress dialog for normal manual loads.

## 2026-05-29 15:30 UTC

- TMA bench automation can now take the shared HMP bench lock before execute-mode runs, with optional plan-level owner, purpose, timeout, and lock-path settings.

## 2026-05-29 15:05 UTC

- Added a shared HMP bench guard CLI for Codex/hardware automation threads so they can report lock ownership, probe COM3 availability, and avoid overlapping hardware tests.

## 2026-05-29 14:25 UTC

- Added TMA Builder summaries for per-stress/load maximum strain values using per-curve `l0` baselines, and wire-break stress/current reporting when a voltage-limit current collapse is detected.
- Added TMA tangent-intersection transition-current summaries for As/Af on the increasing-current leg and Ms/Mf on the decreasing-current leg.
- Added tangent-intersection transition estimates so Assemble can fill missing As/Af/Ms/Mf temperatures from saved VSM temperature-scan heating/cooling records.
- Enabled Builder automation `rebuild_assemble` commands so section-update recipes can refresh saved Assemble rows without opening the Builder UI, including graph-only samples that do not yet have current-annealing records.

## 2026-05-29 13:38 UTC

- Improved AC susceptibility analysis report readability by formatting 1 kHz
  and higher frequencies in kHz.
- Added report-oriented all-condition overview tables and plots for Origin/DOCX
  follow-up, including SNR, delta chi prime, and high-percent diagnostics.

## 2026-05-29 13:25 UTC

- TMA current sweeps now keep the nominal reverse-current leg rate-limited when the supply remains near the voltage limit, avoiding an abrupt current drop or premature plateau transition.
- TMA current-hold recovery now bypasses the persistence wait when filtered stress/load is moving rapidly away from the target, so transformation runaways get an immediate mechanical correction.
- TMA current-hold recovery now keeps predictive multi-step corrections during rapidly moving-away transformations instead of throttling to motor-step corrections just because the previous feedback worsened.
- TMA current-hold recovery now treats large off-target stress/load errors as actionable even when the filtered balance window is noisy.
- TMA current-sweep recipe files now round-trip the disabled "return to start target" setting instead of forcing it back on during save/load.
- TMA settings persistence no longer silently re-enables "return to start target" while closing or saving app settings.
- TMA session metadata now preserves an earlier fault stop reason when the app closes afterward.
- TMA control trace rows can record row-local task text so diagnostic traces do not inherit stale current-sweep task labels.

## 2026-05-29 12:35 UTC

- TMA Logger Sample tab now opens scrollable, vertically stacked current-annealing previews from the connected `.pydpj`, reusing the selected composition/microwire when possible.
- Replaced the manual `Import sample info` button with `Show annealing` because sample import already runs automatically from the connected Builder project.
- The TMA wire diameter field now displays micrometers while preserving millimeters internally for recipe and stress calculations.

## 2026-05-29 10:18 UTC

- Prevent voltage-limited TMA current sweeps from jumping back to the nominal maximum current after the unwind leg; the unwind is kept as the shortened return leg and logged explicitly.
- Let fast moving-away current-sweep stress/load errors enter current hold immediately instead of waiting through the normal confirmation delay.
- Let clearly large current-hold recovery errors bypass the persistence timer, while keeping persistence gating for smaller filtered errors.

## 2026-05-29 09:55 UTC

- TMA Logger Sample tab can now connect a fabrication-data folder, index it without blocking the UI, suggest compositions and microwires while typing, and use fabrication diameters as a fallback when the connected `.pydpj` project has no diameter for the selected sample.
- Large fabrication database roots are staged: TMA loads top-level composition folders first, then reads only the selected composition subtree for microwire/diameter suggestions.
- `.pydpj` sample import remains the preferred diameter source when both project and fabrication data are available.

## 2026-05-29 09:53 UTC

- Log the TMA dashboard task text in both UI telemetry and control-trace CSV files so recipe phase flicker can be diagnosed from saved runs.

## 2026-05-29 09:03 UTC

- Add a repeatable AC susceptibility analysis CLI/module that converts completed logger TSVs and an empty-coil baseline into apparent complex susceptibility tables, rankings, report Markdown, and PNG plots.
- Add Origin-ready CSV exports and a handoff document for finishing Origin/DOCX reporting on a Windows PC with Origin installed.
- Record excitation and sensing coil geometry metadata in the analysis workflow.

## 2026-05-28 15:59 UTC

- Prevented TMA current-hold recovery from treating one-sided transformation scatter as target recovery; the noise band now only accepts/restarts the current ramp when the recent load/stress window overlaps the target.
- Kept current-sweep target acceptance tied to the requested/noise tolerance instead of the motor-step physical floor, so short or stiff wires no longer treat very large stress errors as "reached" while the ramp keeps heating.
- Added a TMA control-trace replay diagnostic script for identifying current-sweep accept decisions that were only accepted because of the motor-step physical floor.
- Added automatic control-trace replay diagnostics to unattended TMA bench summaries after each saved run.
- Added 30 MPa current-ramp speed-ladder recipes, a guarded bench-plan example, and a ramp-speed comparison script for choosing the precision/time tradeoff from saved run folders.

## 2026-05-28 11:39 UTC

- Fixed Current Annealing Logger output so accepted measurements are logged once, leading zero-current readbacks are not plotted as data, run metadata reflects current loop/reverse settings, and repeated cycles use the same color palette as the PyPlot Current Annealing plugin.

## 2026-05-28 11:19 UTC

- Added a TMA automation indexer script for building CSV/JSONL manifests from run metadata folders.
- Added regression coverage for extracting automation run metadata and writing the manifest files.

## 2026-05-28 10:42 UTC

- Added a bench-automation-only option to continue tensile slack take-up after current-sweep mechanical load loss, while normal operator recipes still stop on the same condition.
- Added an optional bench guardrail override for the current-sweep correction travel cap so automated slack take-up can pull far enough to re-tension the wire.
- Discard stale stopped-run resume state when the visible recipe controls have changed, preventing an older 50 MPa current sweep from resuming under a newly edited target start.

## 2026-05-28 10:25 UTC

- Polished the TMA recipe UI by hiding recipe save/load controls behind a Settings toggle, moving uncommon setup/manual-action controls into collapsed detail panels, and adding restore-defaults buttons for setup, current-sweep advanced caps, and manual actions.
- Changed current-sweep first overheating from repeating the first normal target to running one configurable fixed-stress preheat sweep before the normal target sequence.
- Separated first-overheating, target, and current-sweep controls into compact recipe sections; first-overheating shows the load equivalent beside its stress target, and return-to-start is implicit instead of a visible checkbox.
- Displayed TMA sample diameters in micrometers in operator-facing recipe/project labels.

## 2026-05-28 07:47 UTC

- Updated TMA PyPlot current-sweep graphs to show stress/load legend labels, compact whole-mA current-density plus wire-diameter hints in the X axis label, and compact `l₀` context in strain Y-axis labels.

## 2026-05-27 16:58 UTC

- Polished the TMA recipe panel with a collapsed length-setup summary, a recipe-level setup enable switch, and saved/unsaved recipe-file status.
- Rounded load-equivalent labels to 3 decimal places and rendered current-density units with a superscript 2.
- Documented that setup is normally enabled but can be disabled in saved recipes for controlled automation or diagnostics.

## 2026-05-27 14:58 UTC

- TMA paused-current recovery no longer stops only because held-current transformation corrections exceed the per-target no-response travel counter.
- TMA paused-current recovery now uses the configured fast current-sweep stage-speed cap while stress/load is far outside the held-current recovery band, and avoids forcing those large-error transformations down to one motor step after a worsening feedback sample.
- TMA paused-current recovery no longer waits a full filter window for an unchanged filtered signal while stress/load is still far outside the held-current recovery band.
- TMA paused-current recovery now keeps the current ramp held after a single accepted recovery seek until either the filtered resume band or repeated accepted recovery seeks confirm stable recovery.
- TMA current-hold entry now confirms transformation onset faster using an automatic tolerance-scaled sustained-error band, so current ramping pauses closer to the first target departure without a fixed MPa entry floor.
- TMA large-error held-current recovery keeps the fast recovery trigger tied to the default 30 MPa band even when the per-move held-current correction cap is raised for a specific recipe.
- TMA current sweeps now throttle the increasing-current ramp clock briefly after held-current recovery so the next thermal step does not immediately outrun stress recovery.
- TMA current-sweep target ramps now stop as mechanical load loss/slack if the stage travels after l0 while measured load/stress remains near zero; this guard does not infer electrical contact loss because current may still be flowing.
- Added a 50 MPa iso-stress current-sweep recipe for 1 mA to 50 mA and back.

## 2026-05-27 12:45 UTC

- Fixed packaged launcher builds so Current Annealing Logger and TMA Logger open from their separate child processes instead of starting a second launcher window.

## 2026-05-27 09:50 UTC

- Added a Microwire Data Builder startup setting for opening the configured database folder's current `*_latest.pydpj`.
- Made recent database working/archive projects resolve back to the database folder's `*_latest.pydpj`.

## 2026-05-27 09:30 UTC

- Skipped Current Density table auto-sizing during Microwire Data Builder project-load batches to reduce copied project load time.

## 2026-05-27 09:28 UTC

- TMA dashboard plots now reserve AC-dashboard-style tile margins, spacing, and minimum plot sizes so lower-row axes are not clipped by the run log area.

## 2026-05-27 09:25 UTC

- TMA now treats native USB Tic control as required for recipes; `ticcmd` stays available for explicit diagnostics instead of silent recipe fallback.
- TMA hides source-control metadata subprocesses on Windows so recipe startup does not flash transient console windows.

## 2026-05-27 09:21 UTC

- Launch the AC Susceptibility Logger as a separate experiment process from the PyPlot launcher, matching TMA and Current Annealing.
- Hide the detached AC PSU watchdog console window when starting a hardware sweep on Windows.

## 2026-05-27 08:55 UTC

- TMA recipe preflight now reports Tic status read failures as a busy/unreadable controller instead of mislabeling unknown VIN as motor power off.
- TMA unit tests now block accidental real Tic USB access unless a test installs an explicit fake backend.

## 2026-05-27 08:53 UTC

- Added AC susceptibility continuation support that loads previous sweep TSV files, skips fully measured AC settings, and redoes partial settings cleanly before continuing.

## 2026-05-27 08:30 UTC

- Added a detached AC susceptibility PSU watchdog so an app update, crash, or parent-process exit can still zero current, zero voltage, and turn output off.
- Active AC sweeps now refuse ordinary window-close requests until the sweep worker can stop and run its normal PSU shutdown path.

## 2026-05-26 15:41 UTC

- Added a Microwire Data Builder automation database-folder mode that promotes a generated project to `microwire_database_latest.pydpj`, writes `update_manifest_latest.json`, and archives the previous latest files with a timestamp.
- Added `exclude_dir_names` for Builder section update recipes so archived or diagnostic run folders can be skipped during recursive measurement imports.
- Added current annealing support to Builder `update_section` automation recipes.

## 2026-05-26 13:46 UTC

- Changed TMA supply setup so current-sweep and motor-supply channels start unselected instead of using profile defaults.
- Added a shared-broker connection health check before TMA reports the broker supply as connected.
- Let TMA manual auto-connect start a local shared HMP broker when the broker endpoint is down and the operator has explicitly selected the HMP COM port plus supply channels.
- Reordered TMA manual auto-connect so the HMP motor-supply rail is enabled before checking Tic VIN.
- Improved TMA guardrails so current output and motor power cannot be prepared until the operator explicitly selects the wired HMP channels.
- Added the bundled 64-bit `libusb` wheel and updated the Tic native USB backend loader so TMA can prefer native PyUSB Tic commands before falling back to `ticcmd`.
- Let TMA native Tic USB accept a single visible Tic when Windows/libusb cannot read USB string descriptors, while still rejecting ambiguous multi-Tic scans.
- Made preferred-native Tic control fall back to `ticcmd` if an individual native USB status or move command is denied.
- Tightened TMA Tic status handling so device-list output can no longer be treated as motor status; status must include parseable VIN before motor power is verified.
- Logged Tic transport use so native USB activation and every `ticcmd` fallback reason are visible in TMA run logs.
- Serialized native Tic USB status and motion/keepalive commands so status refreshes cannot race motion commands and incorrectly mark motor VIN as unavailable.
- Hid Windows console windows for rare `ticcmd` fallback commands.
- Showed the hardware auto-connect progress dialog during Start recipe preflight when required hardware is not already ready.
- Added `scripts/run_mini_dma_shared_hmp_checks.ps1` for a fast shared-HMP/Mini-DMA/Tic regression slice.

## 2026-05-26 13:37 UTC

- TMA length setup now asks for the mounted wire length once at the beginning, then computes unloaded `l0` from the return-to-zero motion.
- If setup starts above the configured preload, TMA skips the preload ramp and settle instead of asking for a second length entry.

## 2026-05-26 13:10 UTC

- Expanded Microwire Data Builder automation recipes so copied projects can update graph-backed sections such as TMA, VSM, DMA iso-stress, manual stress/strain, and FMR without opening the Builder UI.
- Reduced copied-project load stalls by skipping expensive hidden-table autosizing and thumbnail rendering during project import.
- Renamed the shape-memory stress/strain workflow labels to Manual Stress/Strain while preserving saved project payload keys and column names for compatibility.

## 2026-05-26 12:35 UTC

- Fixed TMA current-sweep voltage-limit recovery so unwind ramps back from the measured supply current if internal setpoint state is missing, preventing an instant jump back to the sweep start current.
- When a current sweep is already paused for target recovery, voltage-limit detection now keeps the held current instead of overriding the hold with unwind.
- Moved TMA wire-break stop/recovery prompts onto the UI thread so a wire break cannot freeze the app by opening recovery UI from the control worker.

## 2026-05-26 11:18 UTC

- Fixed shared-HMP Current Annealing runs so broker-mode measurements are written to the log file as well as the live graph.
- Normalized TMA shared-broker supply readbacks to include resistance and power fields required by logging and live status updates.

## 2026-05-26 09:45 UTC

- Added TMA emergency session recovery when final metadata writes fail because the output folder was moved or temporarily unavailable.
- Removed the current-sweep "Settle after current" setting and post-sweep settle step; current recovery remains handled by the current-ramp hold controller.

## 2026-05-26 07:52 UTC

- Let non-elapsed AC live plots use a deeper history window while keeping elapsed-time traces capped to recent raw samples.
- Hide gridlines on AC live plots for a cleaner dense multi-axis view.

## 2026-05-25 16:45 UTC

- PyPlot Launcher now starts TMA Logger and Current Annealing Logger as separate experiment processes.
- Child experiment processes are tagged with experiment metadata and scrub inherited headless Qt environment variables before launching.
- Documented the launcher-level process separation for hardware experiment windows.

## 2026-05-25 15:58 UTC

- Added a shared Windows sleep-prevention guard for active experiments.
- TMA Logger now keeps the PC awake while a session is running and releases the guard when the session stops or the window closes.
- Current Annealing Logger now keeps the PC awake while an annealing process is running and releases the guard during safe shutdown or window close.

## 2026-05-25 10:42 UTC

- Changed TMA current-sweep plots to use line+symbol curves by default.
- Changed TMA PyPlot defaults to recalculate strain-current curves against one shared global-minimum baseline and show the top power axis, with a setting for per-target baselines or raw measured strain.
- Kept PyPlot-style titles, axis labels, and legends in the Microwire Data Builder current annealing graph display.
- Embedded parsed graph payloads in Microwire Data Builder `.pydpj` project saves so copied projects can restore graph records without depending on the global Builder cache.
- Added the first copy-safe Microwire Builder automation recipe path for updating VSM temperature scan sections in copied `.pydpj` projects.
- Suppressed automatic recursive pending-file scans during mini-database section construction to reduce launch-time stalls when saved sections point at large folders.

## 2026-05-25 10:25 UTC

- Added optional Shared HMP broker mode to TMA Logger so it can use channel-scoped broker leases for current-sweep and motor-supply HMP channels while preserving direct serial supply profiles.
- Added TMA broker host/port settings and preflight behavior that keeps shared-broker mode from silently switching back to serial auto-detect.

## 2026-05-25 10:04 UTC

- Show the active LCR excitation amplitude in the AC sweep status text.

## 2026-05-25 09:52 UTC

- Show per-condition medians on non-elapsed AC live plots so dense long-run data remains readable while raw TSV logging stays complete.

## 2026-05-25 09:35 UTC

- AC Susceptibility Logger now keeps Windows awake during active microwire current sweeps and retries PSU shutdown by reopening the selected serial port if the existing handle fails, so error paths still attempt to zero current, zero voltage, and turn output off.
- AC Susceptibility live plots now retain and render a smaller recent preview instead of redrawing thousands of old rows, keeping long overnight sweeps responsive while preserving complete TSV output.

## 2026-05-25 09:35 UTC

- Apply explicit LCR setup defaults during AC susceptibility runs: auto range on, auto LCZ off, 30 ohm source resistance, ALC on, DC bias off, and comparator off.
- Migrate older current-excitation defaults that started at 1 mA to the full LCR current range from 100 uA to 20 mA.

## 2026-05-25 09:19 UTC

- Wait for the LCR meter to finish switching to the measurement page before sending setup commands, avoiding startup SCPI `*E02` errors during AC susceptibility runs.

## 2026-05-23 14:31 UTC

- Added `uv.lock` and made uv the preferred environment sync path for PyPlot development and Codex worktrees.
- Kept `requirements.txt` and `requirements-win.txt` as pip compatibility exports for machines and packaging scripts that still need them.
- Pinned the PyQt Qt runtime packages explicitly so uv-created environments keep the tested PyQt6/Qt runtime pairing.
- Added a Microwire Data Builder storage-root override for tests so automated runs can isolate mini-database state away from user app-data folders.
- Tightened Windows Codex setup so it checks for Python 3.14 with the `py` launcher before running `uv sync`.
- Shortened Windows pytest temp paths when needed so deep Google Drive fixture paths do not exceed Windows path limits.
- Kept TMA recipe-completion tests headless on Windows by stubbing recovery hardware preflight.
- Sanitized serial logger output filenames as well as subfolder names so Windows-invalid characters do not trigger blocking error dialogs.
- Waited for the Microwire EDA worker thread cleanup in its progress-dialog test to avoid Windows QThread teardown crashes.
- Ordered the Windows test collection so the TMA logger tests run before Microwire Builder/EDA GUI tests, avoiding an order-dependent native Qt teardown crash.

## 2026-05-22 13:47 UTC

- Added measured-current feedback for AC susceptibility current sweeps so the PSU voltage limit is adjusted automatically from readback instead of used as one fixed compliance value.
- Low or unreachable OWON current points now log warnings and continue with the measured current; missing actual-current readback before a point still fails safely.
- Documented that `current_actual_a`, PSU voltage, resistance, and power are the source of truth for later AC susceptibility analysis.

## 2026-05-22 12:22 UTC

- Default the AC Susceptibility Logger LCR source to current excitation, with front-panel current presets from 0.1 mA to 20 mA and voltage excitation still available as an alternate mode.
- Interpret bare LCR current-excitation entries as mA while preserving explicit `uA`, `mA`, and `A` suffixes.
- Add LCR excitation current as a selectable live-plot axis and update AC workflow documentation for current-driven coil measurements.

## 2026-05-22 12:21 UTC

- Added the shared HMP4030/HMP4040 power-supply broker foundation with channel leases, model-aware channel validation, serialized SCPI channel operations, guarded global commands, and a localhost JSON protocol.
- Added a Shared HMP PSU Setup utility for confirming channel wiring and saving reviewed bench profiles before shared-output control.
- Added an optional Shared HMP broker supply mode to Current Annealing Logger so it can lease and control only a confirmed current-annealing channel while preserving the existing direct serial mode.
- Documented the shared HMP broker safety model and the current HMP4040 bench-channel example.

## 2026-05-22 09:38 UTC

- Simplified AC susceptibility hardware setup around a single Auto-connect hardware action, with COM port and baud details moved into a collapsed advanced hardware panel.

## 2026-05-22 09:33 UTC

- TMA adds HMP4040 support with auto-detect, 115200 baud defaults, current-sweep CH4, and motor-supply CH3 while keeping channels user-configurable.
- TMA dashboard graphs now default to a 500 ms refresh interval and cache older downsampled history so long runs avoid rescanning the full run on each redraw.
- TMA pyqtgraph tiles now keep the run log compact, leave right-edge breathing room, use less dense/thinner major gridlines, and color Y axes to match their plotted curves.
- TMA pyqtgraph tiles now keep empty top/right axes visible as plain frame lines without tick marks or labels when no data axis is assigned there.
- TMA manual setup now shows a modal progress dialog while Auto-connect hardware probes the motor, scale, and optional motor-supply channel.
- TMA manual Auto-connect hardware now prepares the current-sweep supply channel with the configured voltage limit and starting current while keeping that channel output off, so HMP4040 CH4 does not retain stale front-panel settings.
- TMA dashboard plot widgets now shrink correctly in the available panel height and keep the run log shorter so the lower-right graph stays inside the visible window.
- TMA recovery-to-zero now keeps correcting when the measured load is still above the true zero-load tolerance instead of accepting a backlash-limited residual load.
- TMA pyqtgraph tiles now remove gridlines, disable SI-prefix axis scaling for fixed engineering units, and use thin line+symbol traces.
- TMA recovery/setup plots now reuse the same per-quantity colors as the dashboard, and current-hold resume no longer expands its resume band from noisy transformation data.
- TMA dashboard plot tiles now re-cap their maximum height after window resizes so the lower-right graph cannot spill below the visible panel.
- TMA dashboard plot tiles now use a stricter 240 px height cap so all four plots and the run log fit comfortably in a 1080p maximized window.
- TMA migrates saved 1000 ms graph refresh settings to the new 500 ms default so older local settings do not silently keep setup/recovery graphs slow.
- TMA load/stress seeking now waits for the filtered scale-control signal to change after a correction before repeating another load/stress move, reducing chatter from stale median/MAD windows.
- TMA zero-load plateau recovery now accepts the current stable zero-load position instead of driving back through the plateau before finishing.
- TMA current-hold stress recovery now requires a same-direction out-of-band filtered error to persist briefly before moving, so noisy one-window excursions do not immediately become motor corrections.
- TMA metadata now records the app source-control snapshot, including branch, commit, dirty state, short status, and origin URL when git is available.
- TMA metadata now records a control-logic version/profile and a SHA-256 fingerprint over decision-relevant control constants and settings, so runs can be compared by control semantics independent of branch names.
- TMA pyqtgraph dashboard tiles now expand with the available panel height again, keep extra right-edge breathing room for colored axes, and draw unused right axes as neutral frame lines.
- TMA removes the old recipe/session-start zero-load capture checkbox; mandatory setup remains the single source of truth for the zero-load baseline.
- TMA current-hold entry now requires a sustained filtered load/stress error beyond a transformation-sized band, so ordinary target fluctuations keep the current ramp moving.
- TMA current-hold adaptive recovery caps are now expressed through strain/recipe limits rather than a fixed millimeter command cap.

## 2026-05-21 09:37 UTC

- Fixed AC susceptibility current sweeps so missing PSU current readback warnings still obey the configured measure time per point instead of extending that point indefinitely.

## 2026-05-21 07:27 UTC

- Restored the stronger TMA colloquium deck as the main revised presentation, removed the redundant iso-stress comparison slide, repaired distorted image aspect ratios, replotted key graphs with PyPlot logic, and replaced the next-step slide image with a thermal-camera frame.

## 2026-05-21 07:04 UTC

- TMA live dashboard, setup, and recovery graphs now use persistent pyqtgraph widgets instead of redrawing Matplotlib figures for each refresh.
- TMA dashboard plots keep left/right channel axes while updating existing curve data, reducing redraw work during long logged runs.

## 2026-05-20 19:20 UTC

- Added a revised TMA colloquium deck with simpler slide titles, larger text, page numbers, speaker notes, a clearer iso-stress workflow explanation, reduced-clutter result plots, commercial DMA comparison, thermal-camera next steps, and AI-assisted build framing.

## 2026-05-20 17:36 UTC

- Marked the Origin `originext` dependency as Windows-only so macOS Python 3.14 environments can install from the shared requirements file.

## 2026-05-20 14:17 UTC

- Added the TMA colloquium presentation deck for the 2026-05-21 Ni-Fe-Ga meeting.

## 2026-05-20 13:36 UTC

- Moved the AC Susceptibility Logger live dashboard from Matplotlib redraws to
  persistent PyQtGraph plot items so graph refreshes stay lightweight during
  long runs.
- Reduced live-display point density for old parameter-scan data while leaving
  TSV logging complete and incremental.
- Documented the PyQtGraph dashboard behavior and updated AC diagnostics notes
  for displayed-point counts.

## 2026-05-20 09:56 UTC

- TMA setup preload now derives the active ramp rate from the live starting load/stress to the requested preload target, so relaxing from a high preload uses the configured setup duration instead of the nominal zero-to-target ramp.
- TMA length-setup progress now reports the active setup phase and phase percent instead of unstable global recipe tick counts.
- TMA setup stable-time holds now reset when the preload or zero-load target is not actually reached, so the measured-length prompt waits for a continuous stable target during current-sweep setup.
- TMA current-sweep load/stress correction now uses a robust recent scale signal for servo decisions, ignores single-sample balance spikes inside the noise band, and waits for a confirmed filtered reversal before sending the first opposite correction.
- TMA current-hold resume now uses a separate automatic recovery tolerance band, so the current ramp continues once filtered stress is practically recovered instead of chasing final-tolerance fluctuations.
- TMA current-hold recovery now retries after a full fresh filter window even when the median signal is unchanged, avoiding indefinite waits during held-current transformations.
- TMA migrates overlarge saved current-hold correction caps back to the safer default and records the setup linear-unload baseline as the run zero-load scale reference.
- TMA current-hold recovery now resumes the current ramp when the recovery seek accepts the target, waits instead of moving when filtered stress is already returning quickly toward target, and only learns hold-response stiffness from motor moves whose measured load/stress changes in the commanded direction.
- TMA setup now keeps length-setup progress monotonic within each phase, lets the dashboard plot grid shrink to the available window, and breaks plotted lines across hidden/downsampled history gaps instead of drawing diagonal bridges.
- TMA dashboard plots now also break the line between downsampled history and the recent live tail, avoiding misleading diagonal connectors while long measurements are still running.
- TMA now has an explicitly armed `--mini-dma-bench-plan` automation path for unattended recipe sequences, with dry-run validation, setup-length automation, per-run timeouts, summary JSON, and modal-warning suppression so failed preflights do not block overnight control.
- TMA session metadata, status text, and run log now record explicit stop outcomes such as normal recipe completion, manual recipe/session stop, emergency stop, wire break/contact loss, app close, or bench automation timeout.
- TMA now treats changed specimen/condition text as part of the auto-generated output filename identity, so a stale base filename is refreshed before existing-output checks prompt to save as the next run.
- TMA now restores the default `21.200 g` hanging-weight zero-load reference when the current bench sign convention sees real positive balance grams while a saved `0 g` reference would otherwise clamp applied load to zero and drive setup in the wrong direction.
- TMA setup slack take-up now exposes a configurable stiffness-prior step cap and defaults it to `50 MPa`, making pre-contact slack removal much faster while keeping feedback-gated moves bounded.
- TMA mandatory length setup now refreshes the frozen control config after accepted starting length and computed `l0`, so strain logging and subsequent control use the measured setup length instead of a stale recipe-start value.
- TMA setup progress now tracks live preload target error instead of elapsed ramp time, and setup no longer performs a timed zero-load settle after the return-to-zero target is accepted.
- TMA dashboard plots now bridge cached downsampled history into the recent live tail instead of leaving an empty middle gap during long measurements.
- TMA dashboard plots now add view-box data-edge padding so right-edge points are not clipped, and the current-sweep task label stays on the active current/hold phase instead of flickering through short settle steps.
- TMA bench automation plans now support high-stress and wire-break guardrails for unattended current-sweep testing, including current shutdown, stress-target recovery, summary guard events, and stopping later trials after contact loss.
- TMA setup return-to-zero now applies a small strain-based speed floor for tiny residual loads, avoiding very slow one-step unloads near baseline.
- TMA length-setup plotting now snapshots setup samples before drawing, preventing live plot refresh crashes from concurrent sample updates.
- TMA paused-current recovery can now use a local hold-only response stiffness after several confirmed correction samples, allowing faster load/stress recovery during transformations while keeping the frozen current-sweep stiffness and displacement/strain safety rails intact.

## 2026-05-20 08:49 UTC

- Made AC microwire sweeps tolerate transient missing PSU actual-current readbacks after current has already been confirmed, logging WARN rows instead of aborting overnight runs.
- Kept the hard safe-shutdown path for non-zero current points where the PSU reports actual current far below the requested value, and documented the readback/wire-break behavior.

## 2026-05-19 16:15 UTC

- Added graph-only batch filtering for Microwire Word report CLI exports; the filter now requires generated Origin graph descriptors so source-only Assemble labels do not create placeholder-only reports.
- Added DOCX export manifest JSON/CSV files that record exported microwires, graph sections, source paths, source mtimes/sizes, and the copied project used for the run.
- Extended project-backed Word report discovery to include TMA run folders under the Praha measurement root.
- Improved VSM temperature scan Origin export with separate low/high-field Y-axis scaling and cycle-specific colors/labels.

## 2026-05-19 16:10 UTC

- Add configurable repeated-X spread to AC susceptibility dashboard plots so dense current, frequency, and amplitude clusters remain readable.
- Show wire resistance as median values per LCR/current setting while keeping `Rs` and `Ls` as scatter-only traces.

## 2026-05-19 15:07 UTC

- Show expected local finish times for AC susceptibility baseline and microwire sweep estimates, and include finish time in live progress ETA.
- Document the always-on PSU actual-current guard used to stop overnight microwire sweeps when the wire/current path appears open.

## 2026-05-19 13:04 UTC

- Added self-describing AC run metadata snapshots to baseline and microwire TSV files, including LCR settings, acquisition timing, current-loop points, and PSU configuration.
- Added an optional 0 mA reference point before the microwire current loop for OWON setups that cannot regulate below about 10 mA.
- Kept AC PSU profile refresh from overwriting the saved OWON COM port with unrelated serial devices.
- Simplified AC dashboard identification with colored Y-axis labels, primary-axis-only grids, and no in-plot legends.

## 2026-05-19 09:26 UTC

- Improved the AC susceptibility live dashboard with measured-current plotting, optional wire-resistance and PSU-power channels, and an optional far-right Y axis.
- Switched the default AC plot layout to four AC-specific tiles: elapsed time, measured current, frequency, and amplitude.
- Made `Rs` and `Ls` plot as scatter-only by default, while wire resistance uses line plus symbols.
- Added display-space horizontal spreading for repeated current, frequency, and amplitude points so dense scans do not collapse into vertical stripes.
- Added optional UI timer telemetry to AC diagnostics so plot/UI responsiveness can be reviewed separately from acquisition logging.

## 2026-05-19 07:40 UTC

- Remember AC susceptibility PSU port, baud rate, and voltage-limit settings separately for each supply profile so OWON and HMP selections no longer overwrite each other.
- Verified the bench LCR-6200 on COM9 and OWON SPE6102 on COM11 with a short live readback check that shut the PSU output back off.

## 2026-05-18 16:25 UTC

- Add PSU resistance and power readback columns to AC susceptibility microwire sweep logs so current-path behavior is visible alongside LCR values.
- Base microwire sweep progress on the planned setting/current/time position instead of raw elapsed time so communication overhead does not make the progress bar show 100% and ETA 0s while the sweep is still running.

## 2026-05-18 15:58 UTC

- Clamp AC susceptibility OWON SPE6102 voltage setpoints to the bench-tested SCPI maximum of 61 V so the supply does not silently keep a zero-volt setpoint when a 62 V limit is requested.
- Migrate older saved OWON AC voltage limits of 5 V, 60 V, or 62 V to the safe 61 V default while preserving intentional lower user limits.

## 2026-05-18 15:45 UTC

- Changed AC microwire current sweeps to wait briefly for PSU actual-current readback after setting each current point before starting LCR reads.
- Kept zero-current/dropout readback as an abort condition after the current has been accepted, so open-circuit or broken-wire failures still stop the run.
- Made AC PSU shutdown set current and voltage to zero before turning output off.

## 2026-05-18 14:55 UTC

- Added a PSU identity preflight for AC current sweeps so non-SCPI serial devices, such as a scale accidentally selected as the current supply, fail before voltage/current/output commands are sent.

## 2026-05-18 14:50 UTC

- Reset AC susceptibility live plots whenever a new empty-coil baseline or microwire current sweep starts, so stopped/partial runs do not remain mixed into the next run.

## 2026-05-18 13:55 UTC

- Made AC microwire current sweeps fail fast when PSU readback does not confirm actual current flow.
- Logged a failure row before aborting so misleading requested-current data is not mistaken for delivered-current data.

## 2026-05-18 13:35 UTC

- Fixed AC microwire current sweeps failing to start when the selected PSU COM port was already open by the logger's inherited connection controls.
- Normalized Windows serial resource strings so malformed COM path variants are passed to pyserial as plain COM port names.

## 2026-05-18 12:17 UTC

- Separated AC susceptibility current-supply settings from Current Annealing
  Logger supply settings so HMP/OWON choices no longer leak between the tools.
- Switched AC susceptibility live plots to small scatter markers and added
  display-only per-condition thinning for dense frequency/amplitude plots.
- Fixed time-based AC progress completion text and kept slow-LCR retry fallback
  measuring the full requested point duration after bounded retries are
  exhausted.

## 2026-05-15 13:23 UTC

- Move TMA recipe/control ticks onto a worker scheduler with frozen run-start settings so Qt repaint lag and Matplotlib redraws do not pace hardware control or CSV/control-trace logging.
- Serialize TMA PSU serial access between worker current commands and UI readbacks, correctly parse scientific-notation current replies, add a current-sweep channel selector, and reset the current channel to output off at `1 V` / `1 mA` whenever automation stops.
- Tighten the TMA dashboard header so the current task uses a fixed single-line row, remove the redundant scale-rate cell, lighten live-plot markers/lines, keep older downsampled plot points visually stable, and remember current-sweep target ranges separately for iso-load, iso-stress, and iso-strain modes.
- Include the current-sweep recipe type in auto-generated TMA output base filenames, for example `iso-stress` or `iso-strain`.
- Let TMA setup finish from a stable near-zero plateau during linear-unload fallback instead of waiting indefinitely for an unreachable fitted zero-stress position.
- Stabilize TMA current-sweep task text during worker ticks and keep scheduled CSV rows flowing while iso-strain current sweeps are already inside target tolerance.
- Close/delete TMA setup and recovery child dialogs cleanly and suppress recovery prompts during window shutdown so a completed run cannot leave the main window trapped behind stale dialog ownership.
- Add a constant-current stress-strain recipe that seeks a chosen load/stress/strain start target, then applies fixed open-loop displacement or strain steps up to a target and optionally back down at each configured current, holding/logging after every step without correcting load fluctuations away.
- Remember TMA dashboard plot channel choices separately per recipe type, using the existing global dashboard layout as the fallback for recipes that have not been customized yet.
- Name constant-current TMA output folders with an `iso-current` token, clamp the fixed step-back leg at its remembered mechanical start position, and avoid hidden post-completion origin recovery for that recipe.
- Remove the constant-current stress-strain max-step cap setting and clamp active recipe current commands to at least `1 mA` so continuity/wire-break diagnostics remain powered even when recipe fields are set to `0 mA`.
- Re-zero the constant-current stress-strain scan after each current change, log current-specific zero position, `l0`, and current-relative displacement/strain columns, and use that zero as the step-back origin for the current leg.
- Start setup-preload ramps from the live load/stress value instead of forcing the target clock through zero when the sample is already partly loaded.
- Marshal recipe-completion cleanup and session stop back to the Qt thread so worker-thread completion cannot directly manipulate widgets, timers, or Matplotlib state.
- Add UI telemetry documentation for event-loop heartbeat, live-label cadence, and dashboard graph redraw timing.

## 2026-05-15 13:11 UTC

- Added automatic AC susceptibility LCR cadence recovery for overnight runs:
  high-frequency FAST settings now log a warning, reconfigure the LCR meter,
  discard a short recovery window, and retry instead of waiting for operator
  confirmation when valid readings arrive suspiciously slowly.
- Applied the same recovery behavior to empty-coil baselines and microwire
  current sweeps.

## 2026-05-15 11:37 UTC

- Split TMA live label/telemetry cadence from dashboard graph redraw cadence, defaulting dashboard Matplotlib refresh to 1000 ms while keeping live samples and hardware acquisition independent.
- Added TMA UI heartbeat and graph-refresh fields to `ui_telemetry.csv` so event-loop responsiveness can be inspected separately from plot redraw timing.
- TMA dashboard plots now downsample older displayed points during long runs, preserving recent samples and all logged CSV data while reducing Matplotlib redraw cost.

## 2026-05-15 11:33 UTC

- Moved AC susceptibility baseline and current-sweep acquisition into worker
  threads so Matplotlib redraws cannot slow instrument logging.
- Throttled AC live-plot redraws to a one-second dashboard cadence while still
  flushing every measurement row to disk immediately.

## 2026-05-15 09:52 UTC

- Refined AC susceptibility live plots so frequency X axes are logarithmic,
  frequency/amplitude sweeps use scatter points, and combined Rs/Ls plots show
  legends.

## 2026-05-15 09:27 UTC

- Changed AC susceptibility acquisition from a fixed number of LCR readings per point to a fixed measurement time per point, defaulting to 10 seconds.
- Updated baseline and microwire sweep estimates/progress to use elapsed measurement time so frequency-dependent LCR response rates do not bias stability checks.
- Documented that the logger records every successful `FETC:IMP?` reply during the time window, not necessarily every internal LCR conversion.

## 2026-05-15 08:58 UTC

- Improved AC susceptibility Stop handling so settle waits process UI events and microwire sweep waits shut down safely when stopped.
- Made empty-coil baseline files flush each row while measuring, leaving usable partial TSV files after Stop or interruption.
- Let auto-detect reuse an already connected shared PSU selection instead of probing the same open COM port again.
- Updated LCR monitor-off command normalization and documented LCR comparator/status values seen during live empty-coil checks.

## 2026-05-14 11:49 UTC

- Refined the AC Susceptibility Logger UI so the sticky actions, point-acquisition labels, filenames, and live plots are specific to empty-coil baseline and microwire current-sweep workflows.
- Updated OWON SPE6102 AC sweep defaults to a 62 V voltage limit and migrated older 5 V/60 V OWON defaults when OWON is selected.
- Replaced the quick AC plot selectors with a Mini-DMA-style configurable plot dashboard, with `Rs vs DC current` and `Ls vs DC current` as the default live views.
- Removed startup PSU auto-detection from normal launch so safe serial `*IDN?` probing only runs when Auto-detect instruments is requested.
- Unified empty-coil baseline and microwire sweep acquisition around one `LCR readings/point` setting, defaulting to 10 reads, and added a sticky AC progress bar above the run buttons.
- Updated the baseline and microwire time estimates so the shared repeated-read count affects both displayed durations.
- Split AC susceptibility output directory and sweep-base persistence from the Current Annealing Logger settings.
- Added live ETA text to the AC progress bar and plot empty-coil baseline reads as 0 mA live points so baseline runs show visible graph activity.
- Added an AC task/status line, interruptible empty-coil baseline stops with partial TSV saving, all-frequency defaults, manual-PSU fallback during auto setup, throttled plot redraws, and optional AC diagnostics mirroring from the Developer menu.

## 2026-05-14 10:48 UTC

- Simplified the AC Susceptibility Logger around the empty-coil baseline and microwire current-sweep workflow.
- Removed duplicate normal-workflow LCR model and PSU controls, reusing the shared PSU selector for AC sweeps.
- Added practical all-frequency/all-amplitude preset actions and defaulted OWON AC sweeps to a 60 V voltage limit.

## 2026-05-14 08:12 UTC

- Added centered ROI streaming for the STM32Cube MLX90640 raw protocol so narrow wire views can run cleanly at 64 Hz while preserving EEPROM-based Celsius conversion in the PyPlot viewer.
- Updated the Cube raw packet parser and capture tool to accept compact ROI packet widths inferred from packet word counts.
- Added STM32 I2C bus recovery before MLX90640 startup to recover from reset-mid-transaction bus stalls.

## 2026-05-14 08:05 UTC

- Prevented mouse-wheel scrolling over AC susceptibility combo boxes and spin boxes from accidentally changing settings.
- Added LCR-6200 range validation and default scan values based on the manual's frequency and excitation limits.
- Added AC setup auto-detection for HMP4030/OWON SPE6102-style SCPI power supplies using a safe `*IDN?` probe.

## 2026-05-13 14:38 UTC

- Added an overnight AC susceptibility sweep mode that measures selected LCR models, frequencies, and excitation levels across configurable current loops.
- Added OWON SPE6102-compatible current-source support alongside the HMP4030-style SCPI path, with safe output-off handling on stop or error.
- Switched the AC susceptibility default toward `Ls-Rs` while keeping optional `Lp-Rp` measurements and LCR-only baseline capture.

## 2026-05-13 13:15 UTC

- Added optional top power axes for current annealing and TMA resistance-current plots, calculated from plotted current and resistance values as `P = I^2R` in mW.

## 2026-05-13 13:00 UTC

- Add TMA measurements to Microwire Data Builder sections and Word report graph exports.
- Pack the Word report Microwire data table top-to-bottom across compact columns.
- Keep VSM temperature scan Word/Origin exports on native dual Y axes with black tick labels on both sides.

## 2026-05-12 16:11 UTC

- Improved TMA current-sweep ETA estimates by adding a conservative hold-time allowance before start and projecting learned hold/correction overhead from completed current-sweep legs during the run.

## 2026-05-12 15:48 UTC

- Added an experimental STM32Cube/HAL MLX90640 raw-stream firmware for the NUCLEO-H753ZI, targeting 16 Hz and 32 Hz sensor refresh with direct I2C Fast Mode Plus subpage reads.
- Added a host capture probe for the raw `MLXR` stream and documented the validated 15.79 packets/s and 31.52 packets/s bench results.
- Updated the thermal camera viewer with a Cube raw protocol mode at 2 Mbaud so the new firmware displays a live diagnostic raw-count heatmap instead of appearing blank.
- Added `MLXE` EEPROM calibration packets and a host-side Melexis temperature-conversion path; the viewer now falls back to raw counts when live calibration sanity checks detect impossible Celsius values.
- Switched the STM32Cube firmware default from 1 MHz I2C to 400 kHz I2C after live bench testing showed the slower bus still sustains about 15.77 fps and restores valid Celsius calibration.
- Added a Cube raw refresh-rate selector in the viewer and serial firmware commands for switching between 16, 32, and 64 Hz without rebuilding; the firmware now uses interleaved subpage reads, minimal auxiliary reads, and interrupt-driven UART packets, making 32 Hz clean while 64 Hz remains overrun-limited at about 51 fps on the valid 400 kHz I2C path.
- Forced the MLX90640 out of chess mode when applying Cube raw refresh settings so the compact interleaved read path no longer displays alternating vertical stripes.
- Added repeatable I2C filter CMake knobs and documented that 1 MHz I2C can reach about 62.7 packets/s at 64 Hz but still corrupts ambient/calibration data on the current bench setup.

## 2026-05-12 10:43 UTC

- TMA adds recipe JSON save/load with descriptive generated filenames for current-sweep recipes.
- TMA adds bench provisioning for copied setups, including HMP motor-supply setup, Tic current-limit application, and pass/fail hardware status reporting.
- TMA updates the HMP4030 current-sweep voltage limit to 32.05 V and defaults the copied-bench motor supply to CH2 at 12 V / 0.5 A while keeping current annealing on CH3.
- TMA keeps the copied-bench Tic motor current limit at the cooler bench-proven 343 mA default and makes emergency stop disable the motor-supply channel as well as the current-sweep output.

## 2026-05-12 00:00 UTC

- Added a TMA PyPlot option to recalculate each strain-current trace with its shortest measured length as `l0`, so its minimum point is displayed as physically zero strain for DMA-style visual comparison.

## 2026-05-11 13:35 UTC

- Added a native `TMA` PyPlot plugin for logger run folders and `measurement.csv` files, plotting strain-current and resistance-current sweeps with one curve per target MPa plateau.
- TMA graph tabs now use shared PyPlot save/popout/formatting behavior and shared Origin export routing.

## 2026-05-11 10:46 UTC

- Added a Thermal Camera Viewer experiment that connects to the Nucleo MLX90640 text-frame stream, reconstructs the 32x24 heatmap live, displays frame statistics, and exports the current frame to Downloads.
- Added a fast 921600-baud binary MLX90640 stream firmware and binary parser path for higher live-view throughput while keeping the text frame-dump mode as a fallback.

## 2026-04-30 11:56 UTC

- Added an AC Susceptibility Logger that reuses current annealing ramp/hold/reverse behavior while logging GW Instek LCR-6200/LCR-6000 impedance readings.
- Added LCR-6000 protocol helpers, a hardware probe script, and documentation for driver setup plus first-pass frequency/amplitude sweep settings.
- Prevented connected LCR configuration failures from silently starting an annealing run, and covered AC log formatting/header behavior with focused tests.
- Added an LCR-only baseline workflow that records repeated empty-fixture or wire/no-current readings without starting the current annealing power-supply path.

## 2026-04-30 11:32 UTC

- Updated Codex worktree setup, README setup, and agent instructions so new worktrees create `.venv` with Python 3.14 instead of Python 3.13.
- Documented the Windows `py -0p` check for missing Python 3.14 registrations before installing PyPlot.

## 2026-04-29 11:53 UTC

- Added Assemble Word sample reports that write one `.docx` per sample and embed generated Origin graph objects as editable Word OLE objects when Microsoft Word automation is available.
- Word reports now appear as an Assemble export format and automatically request Origin plot generation so available graph objects can be embedded.
- Added a non-GUI `launcher.py --microwire-word-report` path for exporting sample Word reports directly from a Builder project, assembled workbook, or R vs T CSV.
- Expanded the sample-report template with Assemble sample/fabrication/functional fields, microscope dimensions/images, and fixed graph sections for current annealing, R vs T, VSM, DMA, shape-memory, and FMR measurements.
- Project-based Word report exports now merge saved Builder section rows directly and discover sibling `RvsT` CSVs, so sample reports can include project measurements even when the saved Assemble table is stale.
- Project-based Word report exports now reuse PyPlot/Origin graph generation for available graph families and keep live Origin sessions attached long enough for Word to paste editable Origin OLE objects.
- Assemble Word exports now also route available VSM, DMA, shape-memory, and FMR records through the reusable PyPlot/Origin graph export path before embedding Word OLE objects.
- Word reports now use outline-friendly heading styles, fit embedded Origin objects inside the page, suppress graph/book/sheet captions and Origin descriptor filenames, preserve PyPlot line/symbol styling, and export VSM temperature scans through the normal PyPlot/Origin TScan graph instead of persisted derivative/smoothed/overlay workbooks.
- Word reports now label the opening table as Microwire data, keep empty Assemble-style values in the requested data-column order, route R vs T through the PyPlot Origin export path, and adjust Origin title/legend/secondary-axis placement so R vs T, VSM temperature scan, and dual-axis graph previews are not cropped or overlapped.

## 2026-04-29 09:44 UTC

- Split TMA recipe scheduling into global control, data-log, and UI-refresh intervals instead of per-recipe frequencies.
- Updated displacement, hold, calibration, Hsw, and current-sweep recipes to use timed steps and the global log cadence while keeping hardware polling/readback timers separate.
- Moved global timing controls into `Settings -> Timing...` and let target-ramp seeking advance planned motion between scale updates for smoother setup preload/current target ramps.
- Defaulted G&G request-mode scale acquisition to a 250 ms interval with a longer read timeout so the measured roughly 5 Hz balance response is treated as the hardware limit instead of a fast timeout.
- Documented the verified G&G balance cadence in the TMA hardware profile and refreshed stale scale-communication bring-up notes.
- Added explicit timing settings for Tic status, Tic command-timeout keepalive, and power-supply readbacks as the first Phase 3 hardware-cadence step.

## 2026-04-29 09:34 UTC

- TMA review/test windows can now opt out of saving settings, preventing temporary screenshot or diagnostic values from overwriting the user's saved sample, project, output, and dashboard plot selections.
- TMA load/stress target ramps now wait for fresh scale feedback after each motor correction instead of stacking planned motion between balance samples, and setup preload completion now honors the displayed setup tolerance.
- TMA Phase 3 control keeps Tic command state separate from slower status polling, so calibration micro-moves chain from the last commanded target and data logging no longer blocks on a Tic status subprocess for every row.
- TMA Tic move, halt, zero-position, and keepalive commands now run through a persistent in-app command dispatcher that coalesces stale target-position commands, while retaining `ticcmd` as the command transport.
- TMA now depends on `pyusb` and `libusb-package`, prefers native USB control transfers for Tic commands/status when available, and falls back to `ticcmd`; Developer -> Benchmark Tic Transports can compare both paths without moving the motor.
- TMA load/stress seeking now continues from the last commanded motor target after fresh scale feedback even when Tic status has not refreshed yet, avoiding repeated `Move skipped` loops during setup preload corrections.
- TMA treats backlash-limited near-target reversals as a practical target hold for non-current seeks instead of looping forever on repeated skipped reverse corrections at the end of a recipe step.
- TMA calibration now waits for a fresh post-move scale sample before recording forward/reverse points and writes an `insufficient_data` calibration report when a calibration session is stopped before a full report can be computed.
- TMA completed calibrations now seed backlash, stiffness, and noise for closed-loop load/stress seeking; stiffness is rescaled for the current gauge length, target corrections use the estimated load-path sensitivity, and too-small tolerances are raised to the motor/noise resolution floor.
- TMA backlash take-up is tracked separately from specimen displacement, so raw motor travel remains in `raw_position_mm` while logged tensile displacement and strain exclude reversal take-up.
- TMA load/stress seek speed limits now use the scale feedback interval instead of the faster control-timer interval, and setup preload slack take-up can use the configured slack `%/s` speed instead of being capped by the fine preload correction step.
- TMA setup preload ramps now convert the derived `MPa/s` rate through the stiffness estimate after force starts responding, while slack take-up before force response can use the configured slack `%/s` speed.
- TMA setup zero-load tolerance is now automatic from the `0.005 g` load floor plus motor-step/noise limits, and the old manual zero-load tolerance row is hidden.
- TMA length setup now detects a stable near-zero raw-balance plateau during the post-preload return, uses it as the run's corrected zero-load reference, and returns to the first plateau position before computing unloaded `l0`.
- TMA final current-sweep zero return and manual load-zero recovery now use the same stable near-zero plateau fallback, updating the run zero-load reference and returning to the first plateau position instead of relaxing indefinitely when the balance stops changing.
- TMA scheduled CSV logging now defers while load/stress control is waiting for fresh post-move scale feedback instead of stopping a current-sweep recipe on a delayed log row.
- TMA current-sweep settle steps now keep correcting until the requested load/stress/strain target is reached before advancing to the next plateau.
- TMA load/stress target-ramp and hold corrections in current-sweep recipes now use the target-ramp stage speed as the dynamic ceiling, with actual speed chosen from target error, measured trend, stiffness, backlash state, and scale-feedback cadence.
- TMA current sweeps now detect open-circuit wire breaks when measured current collapses near zero while voltage is at the configured limit, then disable current, stop/save the measurement, and offer displacement recovery instead of continuing the voltage-limit unwind.
- TMA sample/project/output and dashboard plot selections are saved when they change or when a session starts, and restored custom sample/base filenames are no longer overwritten by auto-naming during startup.
- TMA manual move buttons now use true press-and-hold jog control instead of Qt auto-repeat clicks, so held motion follows the configured manual `mm/s` speed more closely even when a jog tick is delayed.
- TMA Manual Actions now include an auto-connect hardware button for setup moves before starting a recipe.
- TMA pytest coverage now isolates the app's `QSettings` backend from the user's real saved TMA settings, and constructor-supplied test output folders no longer replace the saved output folder on a normal close.
- TMA current-sweep balancing now uses the target ramp stage speed as the dynamic `mm/s` ceiling and hides the old correction step/speed controls as legacy settings, so annealing corrections are not artificially capped by stale fine-correction fields.
- TMA length setup now prompts for an approximate starting wire length before preload, uses it to scale the stiffness prior, and writes setup-only `setup.txt` / `setup.csv` logs alongside the normal measurement data.
- TMA now writes every run into a dedicated output folder with stable file names: `measurement.txt`, `measurement.csv`, `metadata.json`, `scale_raw.csv`, `setup.txt`, and `setup.csv`; repeated runs use `_run02`, `_run03`, and later folders without chaining existing run suffixes.
- TMA now cleans repeated `_runNN` suffix chains from restored/typed base filenames, adds an `Open` button next to the output-folder `Browse` button, and treats small load/stress target crossings inside the physical reversal band as a practical hold instead of immediately reversing into backlash-driven hunting.
- Added a detailed TMA speed-control reference with diagrams covering ramp units, sample-driven force feedback, live stiffness scaling, predictive correction distance, dynamic servo hold, and backlash/reversal handling.
- TMA load/stress seeking now waits for expected motor completion before accepting post-move scale feedback, keeps a run-level live stiffness estimate during ramps, uses a wider automatic motor-step tolerance floor, slows to the minimum motor speed near target, and displays live speed equivalents in `mm/s`, `g/s`, `MPa/s`, and `%/s`.
- TMA load/stress tolerances are now automatic from a `0.005 g` starting floor plus motor-step/noise limits, and the old left-panel Overview is replaced by fixed-width dashboard cells for live speed and hardware state.
- TMA live speed/hardware cells now live inside the dashboard header, setup preload slack take-up treats tiny near-zero residual loads as slack so it can use the configured slack `%/s` speed, and zero-load plateau fallback uses the center of the stable raw-balance band before returning to the first plateau position.
- TMA current-sweep servo corrections now cap predicted move distance by specimen strain percentage, defaulting to `5%`, and cap correction speed by both `%/s` and a hard `mm/s` motor ceiling so long and short wires scale more consistently.
- TMA current sweeps keep the requested current ramp static during ordinary servo error; operators should lower the fixed current-ramp rate when thermal-history control matters instead of relying on automatic current pauses or stress-triggered unwind.
- TMA setup/final zero-load return no longer accepts a high residual load as zero just because the ordinary stiffness/backlash tolerance band is wide; the stable near-zero plateau fallback remains the baseline update path.
- TMA load/stress seeking now uses a hybrid far/near controller: far from target it can keep the motor moving and revise the prediction on each fresh scale sample, while near target or suspicious feedback still uses conservative post-move scale gating.
- TMA speed-control docs now distinguish the 4-5 Hz scale reply rate from the slower near-target correction frequency and include diagrams for hybrid far/near servo behavior.
- TMA gated load/stress corrections now treat recipe/dynamic speed as desired average speed over the full correction cycle and raise the instantaneous motor command speed to compensate for settle plus scale-response dead time, while still respecting the hard `mm/s` and `%/s` caps.
- TMA motor displacement calibration now uses a provisional `800 steps/mm` default for the expected 1/8-microstep configuration, migrates only old saved `100 steps/mm` defaults, and adds an external-gauge motor step calibration workflow that moves by raw Tic units, writes CSV/JSON logs, reports fit quality, and does not apply the result by default.
- TMA motor step calibration now keeps a progress window visible for the whole calibration, including slow move waits and accepted external-gauge readings.
- TMA motor step calibration now keeps the Tic command-timeout keepalive active during slow raw-step calibration moves, preventing the controller from stopping after only a few steps.
- TMA motor settings and docs now distinguish mechanical full steps/mm from Tic controller units/mm, documenting the verified `100 full steps/mm * 8 microsteps = 800 Tic units/mm` relationship.
- TMA advanced motor settings now expose mechanical full steps/mm, Tic step mode, and derived Tic units/mm; applying a new Tic step mode updates the controller through `ticcmd` and rewrites the current-position register so physical mm values stay continuous.
- TMA now has a continuity-current monitor for automated measurements, logs raw scale samples during mandatory setup, applies directional load-limit checks during setup preload, and makes setup preload wait for post-move force feedback before issuing the next correction.
- TMA output-collision and setup windows now show the active sample/output identity, and calibration ignores the saved backlash compensation while measuring new stiffness/backlash.
- TMA now replaces stale output base filenames when the current sample identity changes, and setup preload starts by relaxing toward the final preload target if the wire is already above it.
- TMA setup zero-load plateau fallback now uses a run-level corrected zero reference without changing the configured/default `21.200 g` baseline unless the operator explicitly edits or captures a new zero-load value.
- TMA setup preload no longer treats an already-over-target sample as an immediate overload stop when the next control action can relax toward the requested preload.
- TMA setup preload relaxation from above target now stays under the setup-time preload ramp cap instead of using cruise feedback or the global motion-speed ceiling.
- TMA calibration now caps the load-equivalent plateau acceptance band so an inflated live stiffness estimate cannot mark a preload target reached while the measured load is still far away.
- TMA setup now derives the engaged-wire preload ramp from setup time, uses a separate `%/s` slack take-up speed before force response, relies on the global motion speed as the hard stage-speed ceiling, and quantizes applied calibration backlash to achievable Tic units.
- TMA setup return-to-zero and automatic post-calibration return-to-start now use a setup return-time-derived speed instead of the global motion speed directly.
- TMA now exposes the shared return-time setting in Manual Actions, uses it for manual displacement recovery and post-recipe return-to-start, keeps setup/recovery popups plotting fresh UI-refresh samples during waits, shows a throttled ETA in recipe progress, and keeps raw-scale elapsed time continuous across setup and recipe logging.
- TMA now uses live Tic acceleration/deceleration settings in move-duration and correction-travel estimates, keeping target-position commands unchanged while making post-move feedback waits and cruise/near decisions less optimistic for short moves.
- TMA recovery now restarts the live UI-refresh timer after a stopped session, and setup zero-load fallback now uses a hybrid stable-plateau gate based on elapsed time plus strain-scaled return travel.
- TMA setup preload now leaves slack `%/s` take-up permanently after the first real load response for that preload target, then uses conservative one-move-at-a-time ramp-capped corrections in both tensioning and relaxation directions.
- TMA setup preload now interprets setup time as the live current-to-target preload span, setup return-to-zero holds its initial time-based unload speed instead of shrinking every near-zero sample, and current-sweep load/stress target ramps start in gated feedback.
- TMA setup preload now treats the first slack-to-taut load jump as engagement rather than trusted stiffness and caps setup preload acceptance so backlash/reversal logic cannot accept a multi-MPa overshoot as the preload target.
- TMA current-sweep load/stress corrections now use the stiffest safe stiffness estimate and cap each planned correction to `10 MPa` as well as the strain limit, reducing aggressive first-approach jumps after setup.
- TMA now treats the applied-load limit as a directional control boundary that blocks or halts only tension-increasing motion, and treats the raw scale display limit, defaulting to `30 g`, as a hard balance-protection interlock that halts automation and blocks ordinary moves until the live display is back below the limit.
- TMA current sweeps can optionally pause the current ramp when load/stress/strain error exceeds a configured band, keep displacement correction active, and resume without a wall-clock current jump once the target recovers.
- TMA current sweeps now freeze mechanical stiffness after setup/calibration, treat backlash as reversal take-up instead of large-error target acceptance, and ramp setup-preload overshoots down from the live overshot load/stress over the configured setup time.
- TMA current-sweep dynamic speed control now controls correction distance as the primary average-speed mechanism: load/stress corrections shrink from coarse target-space caps toward `1 MPa` equivalent and then single motor steps near target while keeping the Tic command speed practical for short moves.
- TMA runs now include `control_trace.csv`, a per-decision seek trace with target, feedback, error, stiffness, correction distance, backlash take-up, command speed, wait reason, and move result for diagnosing closed-loop behavior.
- TMA current-sweep load/stress holds now use conservative gated force feedback with `1 MPa`-equivalent correction caps, avoiding stale cruise corrections that could stack around the target.
- TMA setup slack take-up now caps each pre-contact move by a `5 MPa` stiffness-prior equivalent step, the zero-load plateau fallback accepts a stable baseline sooner, and live plots hide zero-current current/resistance points.
- TMA setup preload and current-sweep load/stress fine correction now shrink to single motor-step moves near target, avoid predictive backlash injection in that fine band, and require extra fresh balance confirmation before repeating very-near-target corrections; `control_trace.csv` now includes motor-step and post-move sample-count diagnostics.
- TMA length setup now computes unloaded `l0` from a fitted linear unload intercept during the post-preload 20 MPa -> 0 MPa return when the taut elastic segment is available, using slack/plateau detection as confirmation instead of the baseline source.
- TMA setup return-to-zero now treats collapse of the linear unload slope as slack onset, commits the fitted zero-stress intercept for `l0`, and returns there instead of continuing to drive the wire farther into slack.
- TMA current-sweep load/stress reversals no longer perform predictive full-backlash take-up, so dynamic correction steps around targets are not dominated by the saved backlash distance.
- TMA zero-load plateau fallback now accepts a stable flat balance after `0.05%` of `l0` or `4` motor units of return travel, and the recovery load-to-zero graph now labels load and displacement with a legend.
- TMA recovery graphs now use more distinct load/displacement colors and keep the x-axis label readable in dark mode.
- TMA setup now keeps the continuity current active before current-sweep recipes, and length setup reuses the committed slack-onset zero position when applying `l0` instead of refitting the baseline after additional low-slope samples.
- TMA current-ramp hold now uses a filtered, noise-adaptive high-side load/stress error so annealing fluctuations do not hold current indefinitely, and large current-sweep stress errors can use faster `10 MPa`/`5 MPa` correction caps before shrinking near target.
- TMA current-sweep settle after each current ramp is now time-bounded instead of waiting forever for noisy annealing feedback, current hold uses the same absolute load/stress error on current-up and current-down ramps, and paused-current recovery can use a larger `20 MPa` equivalent correction cap while the current ramp is held.
- TMA current-sweep recipes now expose the stress correction caps and current-hold filter/noise bands in the UI, show a concise current-task summary, and use a more compact dashboard header with native-font live values.
- TMA current hold no longer has a maximum pause-time stop, and the dashboard header now uses a tighter 3-column live-value grid so the plot area fits better on narrower screens.
- TMA current-sweep recipes now include a `First overheating` option that repeats the first target's current sweep once before continuing to later targets.
- TMA current-sweep load/stress corrections now use a smooth dynamic error-fraction cap with visible sweep/hold hard rails, and current sweeps always include the return-to-start-current leg at each target.
- TMA now exposes an occasional-use `Tare scale` button in the normal Hardware scale controls for balances whose front-panel tare is unavailable while connected.
- TMA current-sweep advanced caps, hold bands, and filter settings are now collapsed behind an expander by default, the hold hard cap default migrates from `20 MPa` to `30 MPa`, and manual jog presses resync from the live motor position when no move is pending so stale targets cannot flip a down jog upward.
- TMA dashboard graphs now append live UI-refresh samples between logged CSV rows, using already-known scale/motor/supply state so plots update smoothly without adding hardware reads.

## 2026-04-28 13:11 UTC

- Updated Codex environment setup, the run helper, and README quick-start instructions to use Python 3.14.
- Documented Python 3.14.4 as the current setup reference build and noted the Windows per-user install fallback.

## 2026-04-28 11:45 UTC

- Made TMA recipe startup run the length setup as a mandatory unlogged preparation step before normal recipe CSV/graph logging begins.
- Streamlined the specimen panel into a `Sample` tab by removing manual gauge-length, preload-zero, Tic-zero, manual session, and optional naming controls; sample naming now always updates from the naming fields.
- Added recipe-side stress/load equivalents, including ramp-rate equivalents, using the current wire diameter, and made those equivalent labels readable in the dark UI.
- Remembered sample naming fields and the last `.pydpj` project, auto-imported matching Builder diameter data on restore/name changes, and marked manual/unimported diameter values in red while still allowing manual edits.
- Added a live length-setup popup graph for load, stress, and displacement with its own pause, stop, and progress controls.
- Changed setup preload/return correction moves to use the setup stage speed and control interval as the net correction-distance cap, so setup preload ramps are no longer throttled by the calibration micro-move correction step.
- Added a Developer-menu run-log file mirror for debugging and changed return-to-start after recipe completion to run as an unlogged recovery popup instead of normal recipe rows.

## 2026-04-28 11:32 UTC

- Fixed the Windows Codex `Run` action so it returns to PowerShell after PyPlot exits instead of leaving the terminal parked inside `cmd.exe`.
- Hardened the tracked `run-pyplot.cmd` wrapper so already-cached `cmd /k` Run commands also skip the pause and close cleanly.

## 2026-04-28 11:06 UTC

- Removed the TMA hardware-tab separate heating program; current is now recipe-owned.
- Changed TMA voltage-limit handling during current sweeps to ramp recipe current back to the sweep start current and continue instead of stopping the whole recipe.
- Made the zero-load hanging-weight reference the default max applied-load ceiling, with the custom max-load setting acting only as an optional lower limit.

## 2026-04-28 11:01 UTC

- Added a Recipe-tab sample reminder so TMA shows the currently selected sample before a recipe starts.
- Changed existing-output handling so repeated TMA measurements can be saved as the next `_run02`, `_run03`, and later filename instead of replacing earlier files.

## 2026-04-28 10:44 UTC

- Added an automatic TMA Calibration recipe that records baseline scale noise, preload targets, forward/reverse micro-move phases, and a JSON calibration report with stiffness, backlash, and stress-strain estimates when geometry is available.
- Split calibration preload seeking from micro-move characterization so bent/stiff calibration wires can be straightened with faster, coarser corrections before fine stiffness/backlash measurements.
- Shared the mandatory preload/return length setup workflow with the Calibration recipe and kept old `calibration_copper` saved settings compatible with the new generic recipe name.
- Made the recipe panel size itself to the visible recipe page so calibration controls no longer leave a large blank area before the start button.

## 2026-04-28 09:46 UTC

- Added a TMA hardware profile documenting the current G&G balance, StepperOnline captive linear actuator, and Pololu Tic T500 controller, including product links, derived motion/load/stress implications, and backlash characterization guidance.

## 2026-04-28 09:26 UTC

- TMA now separates high-rate scale acquisition from slower session logging, writes a raw `<run>.scale_raw.csv` sidecar during active sessions, and adds interval load summary columns to the main CSV.
- Current-sweep recipes now expose separate control and log intervals so closed-loop corrections can run faster than recorded session rows.

## 2026-04-27 14:13 UTC

- Shape Memory Stress/Strain shared Origin export now exports only the plugin's current graph tabs, preventing stale separate load/displacement and stress/strain tabs from being sent when the active layout is the dual-axis overlay.

## 2026-04-24 12:08 UTC

- Require Python 3.14 for the project environment.
- Refresh runtime, plotting, scientific analysis, PDF, Origin, packaging, and test dependency pins.

## 2026-04-24 11:05 UTC

- PyPlot live automation sessions now carry long command timeouts through the session bridge, so slow Origin exports can return a clean success response instead of timing out at the controller.
- Automation-triggered shared Origin exports now suppress blocking success dialogs while still logging the export result, preventing offscreen/live-session runs from hanging after graphs are created.

## 2026-04-23 14:05 UTC

- Fixed shared dual-axis Origin export spacing so Shape Memory Stress/Strain overlays keep the graph title above the top-axis tick labels and center the mirrored top `Strain [%]` axis caption instead of placing it in the legend area.

## 2026-04-23 13:41 UTC

- TMA Logger now keeps the last confirmed stage position separate from the commanded target so strain, stress, and recorded points do not jump ahead of real motion after a move command.
- Hsw distribution seeking now refuses to act on stale or missing balance readings for load- and stress-based control instead of nudging the stage on old force data.
- TMA session metadata now preserves the original creation timestamp across JSON sidecar rewrites during a run.
- TMA now supports active hardware auto-detection for the G&G scale, the serial supply, and the Pololu Tic controller, plus a normal zero-load scale reference workflow that leaves the balance showing real grams while keeping physical/software tare actions as advanced diagnostics only.
- TMA naming now mirrors the other microwire loggers more closely by keeping human-readable microwire tokens like `156/2` in the sample name while using file-safe tokens like `156_2` in the output filename.
- TMA's settings panel now prevents mouse-wheel scrolling from silently changing spin-box and drop-down values, removes horizontal scrolling, and exposes tare actions in the manual setup controls.
- TMA now hides low-level scale and motor driver settings behind a collapsed advanced hardware panel so routine bench controls are easier to understand.
- TMA recipe start now performs a hardware preflight that auto-detects/connects required scale and supply devices, reports missing hardware together, and avoids creating run files until preflight succeeds.
- TMA recipe estimates now switch to minutes/hours for longer runs and include a live progress bar, while the duplicate status-bar log echo is hidden.
- TMA hides the separate heating program for controlled current-sweep recipes because those recipes control current directly.
- TMA now restores stale saved `ticcmd` paths to a discovered local install, clamps tiny saved jog values to a usable minimum, and refuses motor moves that round to the current step.
- TMA now exposes per-recipe displacement or correction move speed for Tic moves, applies it through `ticcmd --max-speed`, and labels manual motion as stacked arrow `Move up` / `Move down` controls that repeat while held and chain from the last commanded target.
- TMA held manual movement now advances the commanded linear position by elapsed time times the configured `Manual move speed`, so a held `1 mm/s` move no longer crawls at the button repeat rate.
- TMA now warns when the Tic VIN motor-supply voltage is missing/too low and keeps resetting the Tic command timeout during active slow moves.
- TMA now shows optional zero-load reference capture inside the current-sweep recipe settings, and closed-loop recipe corrections chain from the last commanded target while using conservative recipe-specific correction step/speed values.
- TMA current-sweep seeking now detects target overshoot, switches to fine reverse correction steps, and can apply measured backlash take-up when reversing direction.
- TMA current-sweep seeking now records feedback samples while correcting load/stress/strain, uses smaller/slower correction moves near the target, and no longer stops merely because load is flat during displacement, which is expected during shape-memory transformation plateaus.
- TMA current-sweep defaults now use the faster copper bring-up recipe of `0` to `9 g` in `3 g` steps and a `1` to `3 mA` current ramp.
- TMA now has pause/resume recipe controls; pause/stop turn the current-annealing output off, stopped recipes can be resumed from the saved step or restarted, and any recipe stop/fault can offer displacement/load recovery.
- TMA can optionally power the motor from HMP4030 CH1 or CH2 during recipe preflight while keeping current annealing on the configured annealing channel.
- TMA recovery now opens a temporary dual-axis load/displacement vs time plot while returning displacement or load toward zero/start, without changing the normal run dashboard; the same recovery actions are also available as manual buttons.
- TMA motion spin boxes now clamp manual edits to the physical motor resolution instead of reverting mysteriously, and load/stress seeking stops on truly stale scale feedback while still waiting for a fresh reading after each commanded move.
- TMA max-load safety now blocks only tension-increasing moves when the live load is already over the limit, so relaxing/downward manual moves remain available to recover the rig.
- TMA now puts `Recipe` first, merges scale/motor/power setup into a lower-priority `Hardware` tab, and splits current-sweep recipes into explicit DMA-style entries for iso-load, iso-stress, and iso-strain.
- TMA recipe summaries and spin boxes now trim zero-only decimals, for example `20 g` instead of `20.0000 g`.
- TMA now includes current-sweep recipes for holding load, stress, or strain while ramping current at a configurable mA/s rate; the app updates current in smaller increments at the recipe interval, leaves zero-current resistance blank, and keeps sampling resistance/mechanics during seeking and settling.
- TMA now displays/logs applied tensile load as a positive magnitude even when the scale reports negative values, keeps raw scale/Tic diagnostics, and separates tensile displacement direction from scale sign so target seeking does not stop because of a sign mismatch.
- TMA now defaults this rig's motion convention to negative raw Tic travel as positive tensile displacement, keeps correcting toward a tighter near-target band inside the broad hold tolerance, stops the session log when a recipe completes, and keeps recovery-to-zero samples out of the recipe CSV.
- TMA load/stress correction now bases each correction on the latest confirmed Tic position and limits target distance by correction speed times recipe interval, preventing slow moves from stacking ahead of the real stage and causing overshoot/undershoot loops.
- TMA current sweeps now advance current from elapsed time, use HMP4030-style 0.2 mA setpoint resolution below 1 A, keep sub-mA precision on the first output-enable command, and throttle supply readbacks during fast automation so current commands do not fight voltage/current queries on the same serial link.
- TMA current-sweep recipes now ramp load/stress/strain targets at configurable g/s, MPa/s, or %/s rates, with a separate target-ramp stage speed so automatic return-to-target moves do not crawl at the fine correction speed.
- TMA recipe progress now counts timed target-ramp and current-ramp ticks, so long elapsed-time sweeps do not appear as a short handful of recipe rows or reach 100% before completion.
- TMA load/stress control now defaults to a `21.200 g` zero-load scale reference for the hanging-weight rig and computes applied wire load from the live real balance reading instead of remotely taring the scale.
- TMA current-sweep recipes can now run an optional zero/preload length setup before annealing: actively seek `0 g` applied load, ramp to a configurable preload stress, prompt for the measured gauge length, return to `0 g`, compute `l0`, and then start the recipe without using a slack/free-transformation mode.
- TMA now has an always-visible `EMERGENCY STOP` dashboard button that stops the active recipe/session, halts the Tic motor, and commands the power-supply output off.
- Added a TMA measurement plan covering the copper-wire first test, the intended isostress current-sweep workflow, saved recipe files, and later dynamic recipes.
- Microwire Data Builder video overrides now tolerate minimal video tables that are missing derived video-length columns.
- Added dedicated TMA regression tests for confirmed-position tracking, stale-scale safety, session metadata stability, hardware auto-detection, zero-load reference wiring, and naming behavior, and expanded import coverage to include the TMA module.
- Added `scipy==1.17.1` as a runtime dependency so `microwire_eda` imports and the related launcher test path work in a fresh project environment.

## 2026-04-23 13:22 UTC

- Added a persistent PyPlot automation session mode in `launcher.py` with `--pyplot-session-start`, `--pyplot-session-state`, `--pyplot-session-send`, `--pyplot-session-close`, and `--pyplot-session-list`, so Codex can keep driving one live PyPlot window across multiple follow-up commands instead of reopening PyPlot for every batch job.
- Added a public `PyPlotWorkbench` automation API for plugin selection, imports, plotting, tab activation, shared exports, project save/load, figure-building, and screenshot capture; the batch automation path now uses the same API instead of private widget pokes.
- Added launcher coverage for the live-session control flow, including a cross-process test that starts a real session, imports data, generates a graph, captures a plot image, and closes the session cleanly.

## 2026-04-23 12:30 UTC

- Shape Memory Stress/Strain now defaults new sessions to the dual-axis overlay layout instead of separate tabs.
- Shared `Open in Origin...` now preserves the active Shape Memory layout, so dual-axis overlays export as dual-axis Origin graphs instead of being regenerated as separate load/stress tabs first.

## 2026-04-23 08:45 UTC

- VSM Hysteresis Loops now keeps the shared PyPlot file/folder/manual-path import handlers instead of rebinding its legacy import UI, so folder import behaves like other plugins.
- The VSM Hysteresis loader now accepts selected directories as sources and expands them through the existing recursive VSM file scan before loading measurements.

## 2026-04-23 08:38 UTC

- VSM Temperature Scan Origin export now keeps a single graph title instead of duplicating the same text on the top X axis title, preventing overlapping titles in Origin graphs.

## 2026-04-23 07:33 UTC

- Current Annealing now gives repeated increasing/decreasing cycles their own color shades and per-cycle legend labels in both Matplotlib and the standalone Origin export path.
- Shared `Open in Origin` export now preserves the actual Matplotlib series colors for exported plots instead of repainting them with the generic Origin palette.
- Removed the redundant Current Annealing plugin-specific `Origin export` settings section so PyPlot uses the shared top-toolbar Origin actions for this workflow.
- Current Annealing import now distinguishes amp-vs-milliamp source data more safely and rejects files that would exceed the expected 1000 mA annealing ceiling after unit detection.

## 2026-04-21 15:30 UTC

- Added a Microwire Data Builder VSM hysteresis preview angle mode that can limit displayed loops to 0° and 90° for better readability.
- Tightened the VSM hysteresis angle filter so `180°` no longer appears in the `0° and 90° only` preview mode.
- Added a Microwire Data Builder VSM temperature preview mode that can show raw or smoothed traces, and improved wide multi-graph preview layout/scrolling.
- Removed unnecessary VSM temp-scan preview PNG copies from the synced Praha Google Drive sample drop.

## 2026-04-20 10:25 UTC

- Add a new PyPlot `R vs T` plugin for temperature-resistance CSV files, plotting measured temperature against resistance with heating and cooling separated in one graph.
- Import RvsT data into shared PyPlot workbooks so the graphs can reuse shared save/export/Origin flows.
- Make the shared `Check outliers...` workflow use local-neighbourhood detection and add a preview graph with flagged points highlighted before removal.
- Speed up the shared `Check outliers...` workflow on large datasets by switching to rolling/vectorized detection and loading preview tabs lazily.
- Replace the multiple graph-export shortcut buttons with a single shared `Save graph...` flow that opens export options before the file picker.
- Add a shared `Remove bad data points...` graph-edit mode with rectangle selection, click-to-toggle point picks, and an always-on-top selection window that confirms point removal without an extra toolbar button.
- Tighten shared `Check outliers...` previewing so plotted columns can be reviewed as row-number scatter plots instead of worksheet cross-plots when axis-role metadata is available.
- Show `R vs T` multi-cycle ramps as separate legend entries (`Heating 1`, `Cooling 1`, `Heating 2`, ...) instead of collapsing all ramps into one heating/cooling pair.
- Apply shared legend defaults when new graph tabs are registered so saved legend orientation settings affect freshly plotted `R vs T` graphs.
- Give repeated `R vs T` heating cycles distinct warm shades and cooling cycles distinct cool shades instead of reusing one red/blue pair.
- Retrigger graph display scaling after programmatic MDI subwindow resizes so cascaded/tiled graph windows keep text and markers proportional to the current window size.
- Make tight-layout `Apply plugin override` store only explicit plugin font recommendations instead of copying full graph geometry into a plugin override.
- Stop tight-layout warnings from silently auto-reapplying saved plugin overrides, preventing plugin overrides from appearing to enable themselves.
- Refresh `R vs T` graph tabs in place after bad-data deletion instead of destroying and recreating the MDI subwindow, and ensure the bad-data confirmation prompt stays above the always-on-top selection window.
- Suppress recursive primary-dock resize/visibility callbacks during startup normalization and retabifying so the side panels stop repeatedly resizing themselves after the session opens.
- Stop ordinary non-maximize subwindow state changes from reapplying the global MDI layout, so manually moved graph windows keep their positions when you activate another graph.
- Remove extra dock-switcher visibility refresh hooks and legacy dock-visibility normalization hooks that could make the Project Explorer/Object Manager widths keep shifting after resize.
- Make hidden/restored graph tabs honor the active fullscreen lock as well as `_global_maximized`, so switching graphs while fullscreen is active no longer opens a small window.
- Keep the current dock width when a side panel is narrowed and hidden, so the next PyPlot session reopens Project Explorer/Object Manager at the last width instead of a larger stale width.
- Reduce startup dock churn by restoring primary dock visibility/width once instead of replaying multiple initial retabify/resize passes on first show.
- Add `R vs T` residual plotting mode so users can plot per-cycle linear-fit residuals alongside the raw resistance-vs-temperature view.
- Move `R vs T` residual plotting to the top plugin toolbar (`Plot residuals`) instead of an in-panel button, matching the rest of the plotting workflow.
- Keep the right-side dock switcher on the outer edge of Object Manager during both initial dock creation and retabify, preventing the Object Manager and its switcher from falling into a stray mini window at the top-left of the app.
- Remove the stale `R vs T` plug-in settings section so fresh sessions only expose the shared `Graph formatting` controls instead of a dead `Origin export` graph-settings button.

## 2026-04-17 09:45 UTC

- Fixed the microscope refresh flow so saved manual `d`/`D` values persist instead of disappearing after a refresh.
- Changed microscope refresh to merge in newly discovered rows and image references without rebuilding or backfilling existing manual values.
- Removed OCR-driven microscope and video extraction paths; those workflows are now manual review and entry only.
- Removed the PaddleOCR/PaddleX/PaddlePaddle dependency chain from the project requirements.

## 2026-04-16 16:07 UTC

- Added a new `TMA Logger` launcher app for early hardware-driven stress/strain work with a serial scale and Pololu Tic-controlled stepper stage.
- The logger now supports scale polling, Tic status and jog commands, software tare and position zeroing, displacement-controlled ramp/cycle/hold recipes, richer session metadata, and TXT/CSV/JSON session export.
- Added G&G scale diagnostics: a probe action, automatic no-data warnings, and UI guidance that G&G RS232 balances need a DB9 null modem crossover rather than a straight-through serial link.
- Reworked the `TMA Logger` UI into a dashboard layout with hardware/specimen/recipe tabs, status cards, naming helpers, safety limits, and a cleaner plot/log split.
- Added integrated current-annealing control with reusable supply profiles, manual output control, live current/voltage/resistance/power logging, and mechanical-plus-heating recipe support in the same session.
- Added preload-aware strain zeroing with explicit `l0` gauge length handling so strain can stay pending until the sample is actually under load instead of during wire straightening.
- Added configurable four-tile plotting with dark-theme-aware Matplotlib styling, selectable channels per axis, DMA/heating/mechanical presets, and a dedicated popup plot editor so the live dashboard keeps more space for graphs.
- Added `.pydpj` specimen import so composition/sample naming and sample diameter can be pulled in from Microwire Data Builder projects for stress calculation.
- Added an initial `Hsw distribution` recipe mode that can step through load, stress, or strain plateaus with configurable tolerance, seek nudge, point count per plateau, and optional reverse sweep.
- Made the left-hand `Overview` section collapsible so the main working layout can prioritize controls and plots while still keeping the status cards available on demand.
- TXT output now follows the existing manual stress/strain column convention so the saved files can be opened directly in the Shape Memory Stress/Strain plotting workflow.

## 2026-04-14 15:05 UTC

- Microwire Data Builder VSM hysteresis previews now default to a `±1000 Oe` X-range so loop differences are easier to compare at a glance.
- Added a saved `Preview range` control in the VSM hysteresis section so previews can switch between zoomed field windows and the full measured range.

## 2026-04-14 08:35 UTC

- Microwire Data Builder microscope refreshes now merge newly scanned microscope files into the existing section state instead of replacing earlier rows when only a subset of images is refreshed.
- Saved microscope review flags, overrides, OCR cache entries, and previously known microscope rows are preserved across partial refreshes.
- Saved reviewed microscope diameters are reapplied immediately after refresh/apply operations, preventing previously reviewed `d`, `D`, and `d/D` values from appearing blank while new microscope files are merged.
- Partial microscope refreshes no longer let empty placeholder entries overwrite previously saved detections for untouched wires, so old OCR/image provenance is preserved when only new microscope files are processed.
- Microscope-only Builder/export rows once again keep `Microscope only` provenance instead of falling back to fabrication-only labels when no fabrication records exist.
- Assemble preview/export now falls back to stored annealing and microscope payloads even when a section's in-memory payload marker is missing, keeping hidden-end filtering and saved measurement data available.

## 2026-04-13 14:35 UTC

- Microwire Data Builder VSM hysteresis and VSM temperature scan table previews now reserve full multi-graph width per row so grouped thumbnails stay full-height and render side by side.
- VSM temperature scan previews no longer stack multiple scans vertically inside one compressed graph slot.

## 2026-04-10 09:28 UTC

- Microwire EDA now prefers copied .pydpj project files, supports raw/per-wire-median/per-wire-best repeated-measurement analysis modes, and writes those choices into report artifacts.
- EDA now derives geometry helper metrics, parses elemental composition columns, and adds dedicated current- and composition-side correlation sections to the report.
- Auto-findings now prefer controllable fabrication signals when summarizing process-to-outcome trends.
- Aggregated EDA modes now preserve rows missing a complete `Composition + Microwire` key, and legacy mojibake diameter headers still map into the canonical geometry columns.
- Fabrication source relabeling now preserves annealing provenance for dual-source wires and no longer fails when older payloads omit the `Data source` column.

## 2026-04-09 09:45 UTC

- Microwire Data Builder now labels promoted sibling rows in Fabrication as fabrication-only provenance instead of implying that every same-draw piece was directly measured.
- Fabrication project reload now preserves saved source paths from `_source_paths` rather than falling back to the human-readable `Data source` label.

## 2026-04-01 12:00 UTC

- Added a dedicated `Universal Video Builder` launcher entry for manual fabrication-video review outside the full Microwire Data Builder.
- Added a single-window fabrication/video workflow that scans connected fabrication roots, keeps fabrication data and linked videos in one table, and supports searchable composition selection plus multi-draw row adding.
- Refined the Universal Video Builder layout so the controls and guidance text stay compact and readable, while keeping missing-video rows red and using a softer review state for manual gaps.
- Made source-video launching more robust by falling back to the native OS file opener when Qt refuses to open a valid local video file.
- Added dedicated `.pydpj` save/load support for the new workflow under the `MicrowireVideoBuilder` project kind.
- Added project docs for the new manual-only workflow and documented that it does not use OCR.
- Fixed the Universal Video Builder so broad fabrication roots are scanned independently of annealing or microscope relevance filters from other builder sections, and ignore temporary `~$` Excel lock files during cataloging.
- Improved the Universal Video Builder add-microwire workflow with a scrollable multi-select draw picker that stays open while selecting several draws, a dedicated fabrication-spreadsheet open action, visible `d`/`D`/`d/D` fabrication columns, and filtering for empty placeholder tail pieces.
- Added `Remove selected row(s)` to the Universal Video Builder so mistakenly added microwire rows can be dropped from the current table without rescanning the fabrication root.

## 2026-03-25 08:30 UTC

- Microwire Data Builder Current annealing now keeps the "other annealing" preview column wide enough for multiple measurements instead of shrinking the individual graphs.
- Microwire Data Builder Shape memory stress/strain now treats saved fracture values and fracture current as one linked bundle, preventing orphan fracture-current values and restoring saved per-file picks after refresh/reprocess.
- Microwire Data Builder Shape memory stress/strain now defaults the graph preview panel closed on first open, removes legacy duplicate current columns, hides internal source bookkeeping columns, avoids rebuilding every preview tab when you only switch between graphs, shows one color-banded row per graph instead of cramming multiple graphs into one row, lets you hide the in-table graph column while keeping the preview panel, shrinks row height back to normal when that graph column is hidden, and keeps placeholder rows blank instead of showing inferred current values without linked saved data.
- Microwire Data Builder Shape memory stress/strain now rebuilds saved-project rows from their stored source paths when live graph payloads are missing, syncs microscope-derived current density immediately after project load, ignores stale cached record payloads that do not belong to the currently loaded section data, drops duplicate blank placeholder rows when reopening saved projects, and includes a `Clear selected values` action so individual graph rows can be reset before re-entering manual picks.
- Microwire Data Builder Assemble now restores shape-memory values from the per-graph source rows and drops empty shape-memory rows after orphan-current cleanup, so current/current-density values do not appear without the matching stress/strain or fracture values and manually picked graph values carry over reliably. Sample-level fallback values can still carry through, but current metadata now stays blank unless the saved values are tied to an explicit graph source.
- Microwire Data Builder Assemble now preserves multiple shape-memory graph rows even when they share the same current, fills fracture current/current-density from the saved graph source when fracture values exist, hides `oe` samples by default behind a `Show oe samples` toggle, and uses microwire-aware tie-breakers when sorting equal strain values.
- Microwire Data Builder Assemble sort now treats numeric-looking text values as numbers, preventing rows like `6.61` from sorting ahead of larger strain values such as `7.68` when the preview is sorted numerically.
- Microwire Data Builder now preserves saved Fabrication and Videos table rows when reopening `.pydpj` projects instead of rebuilding those sections from stale local payload caches, so saved fabrication fields like mass/length/production datetime survive reloads, saved video rows keep their source paths instead of turning fully red, and Assemble/Excel-export row sorting keeps same-sample rows grouped while ordering each sample block by the best matching row for the active sort.
- Microwire Data Builder startup now delays auto-opening the last project slightly so the main window can paint before any project-load work begins, reducing the "Not Responding" feel during launch.
- Microwire Data Builder Assemble now always carries Fabrication metadata as the baseline row data and lets Videos overwrite shared production metrics like core temperature, winding speed, glass feeding, and underpressure when newer OCR-derived values exist, even if Fabrication/Videos are not among the explicitly selected export sections.
- Microwire Data Builder Assemble preview/export no longer creates a `microscope_crops` folder next to Excel output unless microscope crops were explicitly requested from the standalone builder export path, and the preview model now caches grouped row background work so scrolling large Assemble tables is noticeably lighter.
- Microwire Data Builder Fabrication now treats a measured draw as relevant at the draw level, but only promotes sibling pieces up to the last meaningful positive row in that draw workbook. That keeps placeholder tails hidden for draws like `5/4` while still surfacing real sibling rows such as `6/1` through `6/6`. Videos follows the same draw-level relevance filter, and Assemble/Excel export now includes those sibling draw rows as grouped sample blocks instead of only the exact measured piece.

## 2026-03-24 22:06 UTC

- Refactored Microwire EDA into a single canonical analysis pipeline with explicit `run_analysis`, `write_analysis_artifacts`, and compatibility `generate_report` entry points.
- Added copy-safe `.pydpj` analysis for CLI and agent workflows, including findings JSON/Markdown outputs, manifest tracking of the disposable project copy used for the run, and transient Assemble rebuilds from Builder project sections when needed.
- Reframed Microwire EDA around modern measured strain and fracture endpoints, with legacy broke/OK analysis retained only as optional auxiliary context.
- Added composition-split signal tables so cross-composition trends can be compared against per-composition endpoint behavior.
- Added `docs/microwire_eda.md` and updated Builder docs to describe the autonomous workflow, RF_EDA alignment, and copy-before-analysis rule.

## 2026-03-24 18:47 UTC

- Fixed Microwire EDA canonicalization so duplicate alias columns are merged before downstream analysis, preventing suffixed duplicate fields from being ignored.
- Fixed Microwire EDA report generation to honor `export_png_bundle=False` while still producing HTML and optional PDF figure output.
- Fixed video review override tracking so propagated draw-length edits record sibling history, show overwrite highlighting/tooltips, and support restoring prior values.
- Fixed video review completion flow so blank `Notes` no longer blocks completion or keyboard advance.
- Fixed annealing graph migration to preserve both legacy non-1000 graph columns when upgrading saved data.
- Fixed annealing export handling so single follow-up graph assets are stored/exported as scalars and single-item legacy lists still embed in Excel.

## 2026-03-23 15:17 UTC

- Added a separate Microwire EDA workflow that reads only Microwire Data Builder Assemble data from `.pydpj` projects or assembled spreadsheet exports and generates an HTML report, summary workbook, canonical CSV, manifest JSON, and optional figure bundles.
- Added Builder and launcher entry points for Microwire EDA, including `Analysis -> Analyze assemble data...` in the builder and direct CLI report generation from `launcher.py`.
- Added report sections for coverage, strain/stress endpoint summaries, fabrication/geometry relationships, interaction plots, sweet-spot binning, time drift, and gated baseline regression models, plus a visible progress dialog while analysis runs.
- Current annealing now surfaces the simplified `1000 mA + other annealing` graph model in the Builder table, Assemble no longer blocks export just because annealing columns are not selected, and the duplicate Assemble `Export worksheet...` shortcut was removed in favor of the main `Export...` flow.

## 2026-03-23 12:48 UTC

- Simplified Microwire Data Builder current annealing rows to use one `1000 mA` anchor slot plus one aggregated `Other annealing` bucket.
- Current annealing previews, worksheet export, assemble/compare graph previews, and HTML export now show `Other annealing` instead of separate low/other mA buckets.
- The Current annealing table now preserves a horizontally scrollable graph layout instead of forcing the final graph column to stretch into view.
- Builder exports now keep all non-anchor annealing files and figures together, while preserving deterministic exact-`1000 mA` anchor selection and warning when the anchor is missing.

## 2026-03-23 12:48 UTC

- Microwire Data Builder video review now distinguishes overwritten cells from first-time fills, showing edited-overwrite cells in amber while keeping newly completed required cells green.
- Video review cells with overwritten values now expose the previous value on hover and offer a restore action from the review dialog.
- The video review `Notes` column no longer shows missing-value warning colours.

## 2026-03-17 14:33 UTC

- Fixed Microwire Data Builder video matching so fabrication videos under Google Drive shortcut folders resolve to the correct draw/piece rows and `Open video(s)` works from the Videos table.
- Updated the Videos workflow to be manual-first, including microscope-style red/green completion highlighting for fabrication fields filled in from video review.
- Fixed fabrication row rebuilding so microscope-only wires can inherit matching fabrication data instead of staying as empty placeholders.
- Stopped the Fabrication tab from borrowing `d`, `D`, and `d/D` from microscope rows; those values now come only from fabrication spreadsheets and remain blank otherwise.
- Scoped Videos refreshes to measured wires only and added a dedicated Fabrication missing-data dialog so long missing-wire lists are readable instead of being truncated in the status text.
- Added possible-source-mismatch suggestions for fabrication rows that still have no matched source files, and highlighted those rows red in the fabrication table.
- Optimized large project loads by batching section imports in memory, suppressing per-section pending scans during restore, and fixed the proxy sort `numpy.bool` error.
- Fixed Videos row actions after sorting/filtering and highlighted entire video rows red when no video source files are available.
- Reworked the Videos review popup into a compact single-row editor with always-on-top behavior, inline total/video-piece length columns, and Enter-to-advance editing flow.

## 2026-03-14 01:20 UTC

- Expanded the figure-layout workflow with panel-label placement/size controls, style presets, minor-tick/tick-direction/scientific-notation controls, manual tick lists, decimal formatting, and reusable figure-template save/load support.
- Added manuscript-oriented workflow actions for `Clone figure`, `Refresh figure`, `Figure sources`, deterministic `Export all figures...` batch export, and automatic house-style application in the figure workflow and automation path, alongside additional paper export formats (`EPS`) and transparent PNG export.
- Added callout-box support plus copy/paste/duplicate annotation tools, snap-to-position guides, arrow style presets, and z-order control so paper annotations can be reused and refined more efficiently across graph revisions and panels.

## 2026-03-14 00:35 UTC

- Extended PyPlot automation recipes so agent-authored jobs can build worksheet-backed graphs from exact X/Y column selections and assemble multi-panel figures from existing graph tabs.
- Added `Paper PNG`, `Paper PDF`, `Paper TIFF`, and `Transparent PNG` shortcuts for faster export of publication-oriented graph and figure tabs.
- Made the figure-layout builder use unit-selectable sizing with `mm` as the default display unit instead of inch-only inputs.
- Added regression coverage for recipe-driven graph/figure creation on top of the new layout builder and persistence support.

## 2026-03-14 00:05 UTC

- Added a shared `Create Figure...` workflow for arranging existing graph tabs into multi-panel publication figures with configurable rows/columns, panel ordering, per-panel title overrides, shared/external legend support, shared X/Y scales, panel labels, spacing/margin controls, and paper-size presets.
- Persisted layout-figure tabs in `.pypj` projects so multi-panel figures reopen with their copied data, legends, and annotation objects intact, and added a `Refresh figure` workflow to rebuild them from updated source graphs while keeping the layout configuration.
- Added regression coverage for layout-figure creation and project round-trip restore alongside the existing graph builder and annotation tests.

## 2026-03-13 21:07 UTC

- Added a shared PyPlot annotation toolbar with text, arrow, line, rectangle, and ellipse tools, plus Object Manager integration so annotation objects can be selected, shown/hidden, recolored, and deleted after placement.
- Expanded the shared format toolbar for graph text and shape editing with font-family selection, stroke width, fill colour for shapes, and mathtext helpers for subscript/superscript text editing.
- Added `File -> New -> Compose Graph...` to overlay visible series from existing plotted tabs into a new composed graph tab, plus a worksheet-backed `Create Graph...` builder for choosing exact X/Y columns and legend labels when creating new graphs.
- Persisted manual/composed graphs and their annotation objects in `.pypj` projects so the extra layout/annotation work survives reopen.

## 2026-03-13 17:26 UTC

- Added a canonical `launcher.py --automation-recipe <job.json>` entrypoint for PyPlot machine-facing automation, including recipe validation, hidden/offscreen execution, `.pypj` load/save support, and machine-readable manifest output.
- Added deterministic batch plot-image export support for automation runs so visible PyPlot tabs can be saved as numbered PNGs for replayable testing and agent workflows.
- Reserved recipe `kind: "builder"` for future `.pydpj` automation without implementing that mode yet.

## 2026-03-11 08:43 UTC

- Added a Shape Memory Stress/Strain section to Microwire Data Builder with static dual-axis graph previews, visibility controls, and PyPlot/Origin handoff actions.
- Included shape-memory graph columns in Assemble, Compare, and HTML export previews so selected microwires can carry the new measurement set alongside DMA, VSM, and FMR graphs.
- Added interactive shape-memory point picking in the builder preview so double-clicked displacement/load/strain/stress values are stored in dedicated columns and can be included in Assemble exports.
- Added fracture-target picking for shape-memory previews so fracture load/strain/stress can be stored separately from the standard picked values and exported through Assemble.
- Renamed the picked shape-memory value columns to plain `Displacement/Load/Strain/Stress` labels, and renamed the older Strain-section outputs to `Legacy strain` / `Legacy stress (MPa)` to distinguish the workflows.
- Added table search across the Microwire Data Builder sections, including the base data/graph tabs and the custom Current density, Transition temps, and Compare views.
- Microscope `oe` filenames are now treated as separate samples, with a Microscope-tab toggle to show or hide those other-end rows.

## 2026-03-10 17:58 UTC

- Fixed PyPlot MDI focus/geometry regressions so project-tree keyboard navigation keeps focus in the tree and resized subwindows reliably refresh their embedded canvases.
- Tightened graph default sizing for the remaining shared PyPlot plugin views used during Origin verification, reducing layout warnings during visual checks.

## 2026-03-10 09:14 UTC

- Simplified contributor guidance to use a single project `.venv` instead of a separate `.venv-wsl` workflow.
- Documented that Codex should create, refresh, or recreate `.venv` and reinstall dependencies when the environment is missing, stale, or on the wrong Python version.
- Clarified that Windows setups must install `requirements.txt` before layering `requirements-win.txt` on top.

## 2026-03-10 08:46 UTC

- FMR Origin export now writes explicit X/Y axis titles, applies per-trace legend labels, and enables anti-aliased non-speed-mode rendering for closer parity with the PyPlot graph view.

## 2026-03-03 08:04 UTC

- Hardened VSM hysteresis crossing calculations against non-scalar/object-array values to prevent scalar-conversion crashes during metrics and plot generation.
- Fixed imported workbook merge handling in the microwire Assembly section so `pd.NA` values no longer trigger ambiguous-boolean exceptions when filling missing fields.
- Hardened microwire key parsing so malformed/non-integral draw/piece indices no longer coerce into unintended grouping keys during database build paths.
- Removed duplicate FMR preview rendering work in the compare graph preview refresh path.
- Added regression tests for VSM coercion edge cases, microwire import-merge `pd.NA` scenarios, and VSM project payload/close-event rebinding safety.
- Extracted shared cursor readout formatting into `plotting/pyplot/cursor_status.py` and improved status-bar readout behavior under tight layouts.
- Added VSM Temperature Scan “smoothed derivative only” behavior so smoothed derivative plots/workbooks can be generated without forcing raw derivative outputs.
- Added VSM Temperature Scan project-state persistence for split/combine flags, derivative toggles, overlay mode, and smoothing-window settings.
- Added Object Manager regression coverage to ensure line-item hide/show controls keep legend entries synchronized even when legends are explicitly present.
- Extended shared navigation/rescale regression coverage to include VSM Temperature Scan canvases (not only FMR-hosted canvases).
- Updated shared navigation mode handling so active `Zoom`/`Pan` follows the selected graph tab instead of resetting on tab switch.
- Hardened subwindow layout redraw to avoid queued Matplotlib idle-draw callbacks hitting deleted Qt canvases during rapid tab/window teardown.
- Added microwire builder coverage for current-density/transition-temp merge behavior and column-group visibility in Assemble output workflows.
- Added VSM Temperature Scan outlier-workflow coverage through shared PyPlot worksheet outlier detection/removal paths.
- Added responsive status-bar width balancing so the cursor readout keeps a readable minimum width while task progress widgets are visible.
- Refined VSM Temperature Scan non-Origin legends/colors: field-pair colors are now deterministic (red/blue then orange/green), legends keep section-order traces, and export legend text no longer embeds section prose.
- Enlarged builder embedded preview defaults for annealing and VSM temperature scans, and expanded regression coverage for preview rendering and dual-axis legend ordering.
- Hardened Current Annealing Matplotlib legend styling so legend text color follows line color after all readability/style passes.
- Modernized the legacy Hysteresis Loops plugin into a native shared PyPlot workflow with `.dat` / `.txt` import support, shared graph formatting, Project Explorer integration, and grouped combined/separate/stacked plotting modes.
- Extended shared import support to `.dat` files so hysteresis loop datasets can be loaded through the standard PyPlot file/folder import actions.
- Updated Hysteresis Loops defaults to use line+symbol traces, `Magnetic field` / `Magnetic flux` axis labels, and reflected scientific-scale units in the Y label instead of a detached Matplotlib offset banner.
- Improved shared graph editing UX: the floating Graph formatting window now keeps its top tab bar fixed/visible, double-clicking plotted curves opens line/marker controls, legend visibility toggles in Object Manager redraw immediately, and legend context menus now expose `Reconstruct legend`.
- Added shared smoke coverage for the Temperature Dependence, Temperature Sensitivity, Stress Dependence, and Stress Sensitivity PyPlot plugins to keep their import-and-generate flows exercised with sample data.
- Added internal launcher CLI automation for PyPlot testing, including plugin selection, path import, plot generation, graph-format opening, screenshots, plot image capture, and JSON summaries for headless validation.
- Migrated the remaining legacy embedded PyPlot entries (`Hsw Distribution`, `Hsw Load Compare`, `Maxion Continuous`, `PDF Plotter`, and `Strain 3D Plot`) onto native shared-plugin wrappers so the registry now runs through the shared PyPlot shell instead of embedded legacy dialogs.
- Added shared connected-folder sources with automatic refresh/import polling plus a manual `Refresh connected` action, and introduced shared `Plot new` / `Replot all` behavior so newly imported files can be graphed incrementally without rebuilding every existing plot.
- Fixed the PaddleOCR PDF experiment so it can be run directly as a CLI script, streams output page-by-page instead of holding the entire document in RAM, and can stop early on low-memory systems before forcing a macOS restart.
- Modernized core runtime dependencies in `pyproject.toml` and regenerated `requirements.txt` from the project spec for lock alignment.
- Updated project runtime metadata to Python `>=3.13,<3.14` to match the supported environment and dependency lock workflow.
- Compatibility note: Origin automation runtime checks remain Windows-only (`originpro`), so Origin export parity validation continues to require a Windows environment.

## 2026-03-02 09:50 UTC

- PyPlot `Check outliers...` now opens a visual preview dialog (tabbed per worksheet) showing the exact flagged rows and highlighted trigger columns before removal.
- VSM Temperature Scan plotting now preserves first-measured order for field/series plotting and legends instead of forcing high-field-first ordering.
- VSM Temperature Scan colors are now direction-aware: heating segments always use warm tones and cooling segments always use cold tones.
- Data Builder VSM Temperature Scan grouping now keeps the parser-provided sample label (including orientation/variants), so samples like `... no glass` and `... no glass 2` remain separate entries.

## 2026-03-02 07:20 UTC

- Fixed shared Project Explorer worksheet activation to open worksheet entries by key (not only path-backed items), and worksheet-group nodes now open their first worksheet when available.
- Hardened shared workbook/worksheet cleanup and Project Explorer focus sync against stale/deleted tree items to prevent runtime errors after closing/removing workbooks or switching tabs.
- Updated tight-layout warning handling so saved plugin graph-option overrides are auto-applied instead of repeatedly prompting for the same plugin.
- VSM Temperature Scan now appends filename-derived orientation tokens (for example `a000`, `a090`) to sample labels when header metadata does not include angle, keeping 0°/90° runs distinct.
- VSM Hysteresis metadata normalization now snaps near-integer temperatures (for example `-29.6`) to integer setpoints (for example `-30`) to avoid duplicate temperature groups/titles.
- Updated visual-check helper to snapshot and restore PyPlot QSettings so temporary visual validation runs do not overwrite the user’s saved import/export directory history.
- Fixed VSM Hysteresis plugin initialization to keep plugin-local settings separate from shared PyPlot settings, so global Graph options remain shared across plugins and persist across sessions.

## 2026-03-01 20:00 UTC

- Shared graph canvas resizing now keeps the configured figure width/height as fixed base/export dimensions and scales display via DPI, so graph content (text/lines/markers) zooms proportionally instead of being compressed.
- Resizing behavior is applied from the shared PyPlot window layer, so all plugins using Matplotlib graph tabs inherit the same fixed-dimension + proportional-zoom behavior.
- Added regression tests for resize-driven display scaling with fixed figure inches and for preserving Graph formatting dimensions after subwindow resizes.

## 2026-03-01 08:55 UTC

- Switched PyPlot plotting workflows to the shared status-bar progress API (removed modal per-plugin progress dialogs in Current Annealing, Stress Dependence, and Stress Sensitivity).
- Updated shared import progress to use the status-bar progress bar instead of a separate modal progress window.
- Reordered status-bar widgets so task progress appears to the right of the live `x/y` cursor indicator.
- Project Explorer now switches graphs on selection change, enabling quick Up/Down keyboard traversal between plot tabs.
- Project Explorer graph selection now preserves tree focus after tab activation so repeated Up/Down traversal keeps working.
- Optimized large Current Annealing plot batches by throttling progress/event updates and reducing repaint overhead while tabs are created.
- Added shared graph-canvas quick actions: `Cmd/Ctrl+C` copies the active graph as PNG to clipboard, and right-click shows `Copy graph as PNG` / `Export graph...`.
- Improved shared graph rescale robustness (including FMR): when Matplotlib autoscale does not update limits, PyPlot now falls back to visible line-data bounds.

## 2026-02-28 19:59 UTC

- Fixed macOS fullscreen graph switching in the shared MDI tab proxy so switching between graphs keeps a single fullscreen subwindow instead of dropping into stacked/cascaded small windows.
- Fullscreen graph geometry now fills the available MDI viewport instead of aspect-fitting to a reduced letterboxed window.
- Current Annealing project persistence now saves and restores loaded data sources plus open/active plot tabs in `.pypj` files.
- Project load now auto-loads data for `auto_load_on_import` plugins when paths/workbooks are present but plugin runtime data has not been restored yet, preventing disabled Plot actions after reopen.
- Added a shared plugin project-state wrapper in PyPlot host save/load flow so all plugins persist/restore common source-selection state consistently, including plugins that also keep custom project state.
- Shared project restore now tracks whether plugin plots were open and regenerates graphs when needed, so plugins without custom tab serialization still reopen with plots available.
- VSM Hysteresis Loops now uses shared PyPlot project persistence/versioning instead of legacy overrides, restoring `.pypj` compatibility with shared host save/load.

## 2026-02-28 19:22 UTC

- Added a Project Explorer search box in PyPlot that filters visible tree items by name, details, and full tooltip/path text.
- Matching child rows now keep their parent branches visible while filtering, and the filter is reapplied automatically as tree content updates.

## 2026-02-28 19:15 UTC

- PyPlot MDI behavior: switching between graph/workbook subwindows now preserves maximize/fullscreen state on macOS instead of dropping back to windowed mode.
- PyPlot graph sizing: activating or resizing shared MDI subwindows now re-fits Matplotlib figure layout to the active canvas, reducing large empty regions after fullscreen/arrangement/tab-switch transitions across plugins (including VSM Temperature Scan).
- Tight-layout warning dialog now supports applying the selected action (keep sizes, auto-fit, or plugin override) to all affected graphs in the current batch.
- Current Annealing plugin plotting now updates the shared status-bar task progress (`_begin_task_progress` / `_update_task_progress` / `_end_task_progress`) during graph generation.
- macOS UI polish: toolbar/tab control buttons now use more native behavior/icons (platform default disabled styling, native titlebar glyphs for tab hide/close controls, and mac-friendly toolbutton raise behavior).

## 2026-02-27 08:20 UTC

- VSM Hysteresis Loops now groups plotted/exported data by sample plus temperature, fixing cross-sample temperature merges in graphs and Origin export selection.
- Added shared PyPlot status-bar task progress for long-running operations, and wired it into shared data import and shared workbook-to-Origin export flows.
- Tight-layout warnings now provide recommended font targets and direct actions (keep sizes, auto-fit current graph, or apply plugin graph-options override).
- VSM legend/theme behavior was aligned with shared graph formatting and dark-mode handling so plugin plots follow shared controls more consistently.

## 2026-02-27 06:44 UTC

- Fixed VSM Hysteresis Loops tab registration and legend refresh paths to apply shared PyPlot graph options (grid, fonts, legend settings) instead of bypassing them.
- Updated VSM plot theme refresh to keep shared `show_grid` and shared font-size defaults intact when legends/theme are rebuilt.
- Fixed shared dark-graph theme toggling to preserve each graph's original grid visibility state instead of forcing grids on.
- Added shared tight-layout warning handling in PyPlot: when Matplotlib cannot apply tight layout, PyPlot now reports the likely oversized text object with the exact font size and logs the full size summary.

## 2026-02-26 18:22 UTC

- Removed the VSM Hysteresis Loops plug-in "Appearance" settings section so it no longer duplicates shared PyPlot graph controls.
- Switched VSM Hysteresis Loops to shared PyPlot workbook/Origin export flow, including shared `Open in Origin...` action routing.
- Updated VSM hysteresis settings/theme handling to remain compatible when legacy style/dark widget controls are absent.
- Updated VSM default loop axes to prefer varying `Applied Field For Plot [Oe]` / `Signal X direction [emu]` columns and kept automatic plot-time fallback to varying axes when selections are flat.
- Fixed VSM metadata parsing so explicit `Set Sample Temperature ...` entries are not skipped by earlier fallback tokens, preventing stray one-off temperature groups (for example `26 °C` outliers).
- Improved shared graph dark-theme restoration so legends reliably return to light styling when `Dark graphs` is turned off, even if legends were created while dark mode was active.
- Added shared Graph formatting legend orientation controls (`Auto`, `Vertical`, `Horizontal`) and wired them into both per-graph formatting applies and saved graph-option defaults.
- Added a shared activation-time subwindow normalization pass for the single-visible-graph case to avoid occasional narrow graph windows after app switching.
- Kept VSM bound methods from overriding shared PyPlot graph/object-manager handlers, so graph names, object manager behavior, and shared graph-format interactions remain consistent.

## 2026-02-24 18:18 UTC

- Added automatic rotation for workspace `logs/message_log.txt` and `logs/crash_log.txt` with a default 1 MiB size cap and five numbered backups.
- Wired both PyPlot and Microwire Data Builder log writers through the shared rotating-log helper so long sessions no longer grow logs without bound.
- Documented the new log-retention behavior in `docs/pyplot.md`.

## 2026-02-23 07:50 UTC

- Shared `Open in Origin` export now creates/updates graph titles through layer-scoped `label -s -n title "..."` plus object-API positioning, improving title visibility reliability across Origin 2026 builds.
- Shared Origin title export no longer uses `title.show` or root-level LabTalk fallbacks, preventing `TITLE.SHOW is illegal name` and worksheet-context `Math cannot be performed on Text column` errors.
- Shared Origin title export now uses `label -s -n title "..."` (plus an object-API/manual-label fallback) instead of `title -s`, matching Origin 2026 behavior where `title -s` can be parsed inconsistently from worksheet context.
- Shared Origin title commands are now executed strictly through the primary layer context and then re-applied after layer rescale, with title position computed from layer ranges so the title reliably renders at top-center in Origin 2026.
- Shared Origin title export no longer writes unsupported `title.just`/page-attach commands, preventing repeated `TITLE.JUST is illegal name` errors.
- Shared Origin graph creation now prefers the `line` template before `ORIGIN`/`scatter` fallbacks to reduce recurring template-side `LEGEND.SMARTPOS` warnings in affected Origin 2026 setups.
- Shared Origin dual-axis export again prefers `add_layer(4)` (`TopXRightY`) first so top/right axes stay linked to the primary layer scales; plain-layer fallback remains for runtimes where preset creation fails.
- Shared Origin export explicitly rescales layers after plotting so load/stress axes are not left in incorrect default ranges.

## 2026-02-20 08:52 UTC

- Manual Stress/Strain Logger: dual-axis overlay cursor readout now always shows both coordinate pairs in one line: `L/D (x, y)` and `S/S (x, y)`.

## 2026-02-19 12:19 UTC

- Shared Origin export: replaced fragile axis-title LabTalk syntax with direct axis-title commands (`label -xb/-yl/-xt/-yr`) so top/right titles for dual-axis overlays apply reliably.
- Shared Origin export: graph title now uses an explicit centered page label (`label -p ... -j 1 -n title`) with large font sizing, avoiding scale-attached title drift.
- Shared Origin export: title placement now explicitly uses page attachment (`title.attach=1`) with fixed centered coordinates (`x=50`, `y=102`) so sample titles stay centered/high regardless of axis ranges.
- Shared Origin export: dual-axis secondary layer now prefers `graph.add_layer(4)` (`TopXRightY`) and applies final axis visibility via direct layer properties (`x/y showAxes/showLabels`) after title assignment, preventing interleaved duplicate tick labels.
- Shared Origin export: removed `layadd` fallback and now relies on `graph.add_layer(4)` only for dual-axis layers, avoiding Origin template-side `LEGEND.SMARTPOS` expression errors in affected builds.
- Shared Origin export: avoids writing `layer.*.showLabels=2` on secondary layers (Origin 2026 can flip `x2/y2` label mode to duplicated labels); side visibility is now enforced via `x.showlabel/x2.showlabel` and `y.showlabel/y2.showlabel`.
- Shared Origin export: prefers built-in templates (`line`, `scatter`) first and only falls back to `ORIGIN`/`<Origin EXE>\\ORIGIN.OTP`, reducing template-script side effects (including recurring legend smart-position errors on some setups).
- Shared Origin export: dual-axis overlays now apply explicit plot colour cycling and auto-hide duplicate secondary-layer traces (stress/strain duplicates of load/displacement labels) after rescale, so legends and visible curves stay uncluttered by default.
- Shared Origin export: logs per-graph template and dual-axis layer-axis snapshots into PyPlot Message Log for runtime diagnosis.
- Shared Origin session startup: now prefers `originpro.attach()` before `set_show()` and verifies automation health early, so `Open in Origin` fails with a clear runtime message instead of delayed OriginExt pointer crashes on stale COM handles.
- Shared Origin export: template resolution now avoids querying `origin.path('e')` during graph creation, preventing side-effect popups on unstable automation sessions.
- Shared Origin export: removed `layer -aa 1` in dual-axis export; on Origin 2026 this command can force both-side axes/labels (`showAxes=3`), producing duplicated/interleaved tick labels.
- Shape Memory parser: drop leading zero-load rows until the first non-zero load point so pre-load baseline zeros are excluded from segmented plotting/export.

## 2026-02-19 11:59 UTC

- Shared Origin export: replaced fragile axis-title labtalk syntax with direct axis-title commands (`label -xb/-yl/-xt/-yr`) so top/right titles for dual-axis overlays apply reliably.
- Shared Origin export: switched graph-title creation to `label -t` + explicit title font sizing, preventing missing graph titles on some Origin builds.
- Shared Origin export: replaced invalid legend refresh command (`legend -o`) with documented legend reconstruction (`legend -r`) to stop repeated `LEGEND.SMARTPOS` Origin errors.
- Shared Origin export: dual-axis secondary layer now uses documented `layadd userdef:=1 ... top:=1 right:=1 ...` creation so duplicate bottom/left axes are not generated.

## 2026-02-18 12:27 UTC

- Shared Origin export: replaced unsupported `PAGE.ANTIALIAS` and fragile axis/title assignment with Origin-compatible label commands (`label -s -n title`, `label -s -xb/-yl/-xt/-yr`) and per-layer antialias fallbacks.
- Shared Origin export: multi-axis worksheets are now grouped by axis metadata and plotted to separate linked layers (`layer -new Both`) so dual-axis overlays export with correct scales instead of an extra near-zero trace.
- PyPlot legends: dual-axis overlay legend rebuild now deduplicates labels across sibling axes and keeps a single host legend, preventing duplicate `Loading 1` entries.
- PyPlot MDI sizing: graph-option and graph-format applies now re-fit and re-arrange subwindows after size changes to avoid one graph appearing larger until focus switches.

## 2026-02-18 10:19 UTC

- Shape Memory Stress/Strain: dual-axis overlay now keeps a single segment legend (`Loading 1`, `Unloading 1`, ...) instead of separate `Load ...` and `Stress ...` legend groups.
- Shape Memory Stress/Strain: the selected graph layout mode (separate tabs vs dual-axis overlay) is now remembered across sessions.
- PyPlot Object Manager: double-clicking a legend now opens the shared `Graph formatting` legend controls so legend settings are consistent with the main formatting window.
- PyPlot MDI windows: hardened visibility-queue cleanup to avoid stale subwindow references that caused `wrapped C/C++ object ... has been deleted` runtime errors while switching/closing many graphs.
- Shared Origin export: per-series axis metadata is taken from the actual source axes (including multi-axis figures), and graph/axis title assignment now uses OriginPro API-first setters with LabTalk fallback to improve title reliability.

## 2026-02-17 15:12 UTC

- Manual Stress/Strain Logger: replaced the idle badge text with a simpler countdown-only display (`Ns left`).
- Manual Stress/Strain Logger: made scale countdown configurable from the UI with a default timeout of 55 s.
- Manual Stress/Strain Logger: made the countdown badge clickable to manually reset the timer without changing the logged load.

## 2026-02-17 13:55 UTC

- Manual Stress/Strain Logger: improved the `Microwire` preset field to use slash-style entry (for example `11/1`) while still generating file-safe names with underscore tokens (for example `11_1`).

## 2026-02-17 09:31 UTC

- Manual Stress/Strain logger: when start displacement is set to 10 points, strain now uses an effective gauge length `L0_effective = L0_input - 0.1 mm` (instead of raw `L0_input`).
- Applied the same effective-`L0` logic to the dual-axis overlay top axis so strain ticks/labels stay consistent with the logged strain values.
- Changing start displacement mode now re-runs derived calculations for existing points to keep strain values in sync.
- Manual Stress/Strain logger: added `Show annealing graphs` using the connected `.pydpj` project, loading annealing source files from the project row and previewing separate **high-current** and **low-current** `Resistance vs Current` graphs.
- Manual Stress/Strain logger UI: compacted the left panel without scrolling by placing related controls side by side (name-builder `Reset` next to preset selector, `Auto-fill diameter` in the diameter row, project action buttons inline), keeping fields like `Notes` visible.
- Manual Stress/Strain logger: improved `Area` label formatting so small cross-sections no longer round to `0` (uses scientific notation for very small values) and displays the unit as `mm²`.
- Manual Stress/Strain logger: logged-data table now includes an extra `Micrometer points` column (derived from displacement), while file export format remains unchanged.
- Manual Stress/Strain logger: the table micrometer column now reflects the wrapped device display (`0..45`, step `5`) anchored to `Micrometer at d=...` and start mode, instead of raw unwrapped point counts.

## 2026-02-17 08:38 UTC

- Current Annealing Logger now preserves key process settings per selected power-supply profile (max/start current, step, hold time, max voltage, channel, reset-on-start, and voltage-limit action).
- Fixed cropped text in the Current Annealing Logger process settings by reflowing the voltage-limit controls into a multi-row layout.
- Updated the Current Annealing Logger sample field so it can be left empty (optional) while still supporting numbered sample values.
- Improved Owon SPE behavior by refreshing the voltage setpoint before each current setpoint update, removing the need for manual pre-adjustment in typical runs.

## 2026-02-13 12:20 UTC

- Fixed shared PyPlot `Export TXT...` behavior for plugins that rely on base actions: the toolbar action now enables from plotted tab data and export falls back to Matplotlib lines when plugin line-state metadata is absent.
- Fixed shared Origin workbook export/session handling so `Open in Origin...` and workbook export keep Origin open instead of immediately exiting the Origin session.
- Restored shared side dock switcher buttons (Project Explorer/Object Manager) across platforms.
- Upgraded shared Graph formatting UI into tabbed sections (`Text`, `Axes`, `Ticks`, `Legend`) and added legend location/font/columns/symbol/follow-color/draggable controls to the same shared window.
- Added `Settings -> Graph options...` with global defaults and optional plugin-specific overrides for shared graph/legend defaults.
- Updated the Shape Memory Stress/Strain plugin to use shared action-state wiring so shared toolbar actions (save/normalize/TXT/Origin) follow plugin/tab readiness.
- Fixed shared side-panel behavior: dock switchers now use click-toggle mode and no longer force Project Explorer/Object Manager visible, preventing resize flashing and allowing panels to stay hidden when toggled off.
- Fixed side-panel width restore on switcher clicks: dock reopen now uses stored/default widths instead of oversized widget `sizeHint` widths, so Project Explorer/Object Manager no longer jump excessively wide when toggled.
- Prevented side-panel width drift across repeated open/close cycles by ignoring transient splitter-driven growth when caching dock widths; side-dock max width clamp is now stricter.
- Side-panel toggle now explicitly persists the current dock width on hide and restores that same stored width on next show, so repeated side-button open/close cycles keep user-set widths stable.
- Fixed shared MDI graph-window arrangement defaults: new graph/workbook windows now open in `Cascade` mode, and shared `Window` menu actions (`Cascade`, `Tile Vertical`, `Tile Horizontal`) are restored for all plugins.
- Cascade arrangement now normalizes graph subwindow sizes to a shared target width instead of reusing stale per-window widths, so simultaneously opened graphs stay visually consistent.
- Fixed MDI maximize/restore behavior so restoring a maximized graph reliably returns to windowed view.
- Locked shared graph subwindow display geometry to the figure aspect ratio (including resize/maximize) so on-screen plot proportions remain consistent with saved output proportions.
- Improved shared legend auto-layout: when orientation is `auto`, dense/long legends prefer vertical layout instead of forcing unreadable horizontal rows.
- Extended shared `Settings -> Graph options...` defaults with `Figure width`/`Figure height`, and apply those defaults when new plugin graphs are registered so graph windows start with consistent dimensions.
- Added shared Graph Options dialog controls for `Apply`, `Cancel`, and `Reset to defaults`; applying now immediately refreshes all currently open graphs.
- Enabled shared legend double-click routing: double-clicking legend content opens the shared Graph formatting window on legend controls.
- Fixed shared cascade activation behavior so selecting another graph no longer resets manually positioned subwindow geometry.
- Stabilized shared MDI aspect-ratio handling to prevent progressive graph-height collapse during focus/task switching.
- Upgraded shared `Open in Origin...` to create Origin graphs from exported worksheets (not only transfer worksheet data).
- Standardized shared Origin metadata mapping for worksheet headers: `Long Name` stores physical quantity, `Units` stores units, and `Comments` stores legend/series labels.

## 2026-02-12 16:07 UTC

- Added shared PyPlot plot-workbook generation for plugins that rely on the base workflow: plotted line data now auto-registers as `Plot data` worksheets/workbooks (XY column pairs) so workbook tooling is available even without plugin-specific workbook code.
- Added a shared `Open in Origin...` fallback for base plugins: the action now exports the active plugin's shared plot workbooks to Origin, fixing disabled/no-op Origin export behavior in plugins such as Manual Shape Memory Stress/Strain.
- Added a shared `_clear_tab_list(...)` tab-removal helper in PyPlot so plugin tab clearing uses the same internal teardown path, keeping plot/workbook state synchronized when regenerating graphs.
- Marked selected plugins with custom workbook pipelines to opt out of shared auto-workbook generation, preventing duplicate workbook entries where plugin-specific workbook registration already exists.
- Added shared `PyPlotPlugin` helper methods for plugin authors (`apply_shared_action_state`, `clear_plot_tabs`, `run_origin_export`) and migrated multiple plugins to use them so toolbar state updates, tab cleanup, and standard Origin export flows are no longer repeated plugin-by-plugin.

## 2026-02-12 11:48 UTC

- Replaced the PyPlot `Check outliers…` placeholder with a functional worksheet scanner that detects statistical outlier rows (IQR method with z-score fallback on low-spread columns).
- `Check outliers…` now presents a detailed per-worksheet summary and supports removing flagged rows directly from affected worksheets, refreshing open worksheet views and Project Explorer row/column counts.
- Added regression tests for outlier finding and in-place outlier row removal in the PyPlot worksheet model flow.

## 2026-02-12 10:41 UTC

- Fixed `VSM Temperature Scan` dual-axis PyPlot legends so combined `10000 Oe` + `50 Oe` plots list series from both left and right axes.
- Updated VSM Temperature Scan Origin export axis-title handling to use named Origin axes (`x`, `y`, `x2`) with robust fallbacks, so exported axis labels now match PyPlot labels.
- Added explicit Origin graph-title application for all VSM Temperature Scan exports (main, smoothed, derivative, and smoothed derivative) so each exported graph shows its full title consistently.

## 2026-02-12 09:23 UTC

- Added a new `VSM Isotherms` PyPlot plugin that parses VIR exports and plots isotherms grouped by sample angle, so `0°` and `90°` measurements render in separate graphs with same-angle temperatures overlaid.
- Added a derived magnetocaloric entropy view (`-ΔS_M` vs temperature) computed from isothermal `M(H)` curves using a finite-difference Maxwell-relation estimate.
- Extended PyPlot import support/documentation for VSM VIR files by adding `.vsm-vir-data` to supported extensions.
- Fixed VSM VIR import handling in the generic workbook loader so `.VSM-VIR-DATA` files are no longer skipped as unsupported during folder import.
- Switched folder import to the native OS directory picker and disabled the custom dock-switcher overlay by default on Windows for more stable side-panel behavior.
- VSM Isotherms now registers imported workbooks automatically (per angle isotherms + entropy sheets), enabling `Export workbooks to Origin...` for this plugin.
- VSM Isotherms now ignores non-VIR file extensions during load and consolidates duplicate same-temperature runs per angle, preferring full-field curves so legends and overlays stay readable.
- VSM Isotherms Derived metrics now accepts user-defined entropy field levels (`ΔH` in Oe) for both plotted entropy curves and entropy workbooks.
- Primary side docks (Project Explorer/Object Manager) now clamp oversized persisted widths to avoid reopening in overly wide states.

## 2026-02-11 16:23 UTC

- Microwire Data Builder `Assemble` tab now includes a quick row search filter (case-insensitive across currently visible columns), with matching-count status text and search state persisted in `.pydpj` projects.
- HTML exports from `Assemble` now include a row search box with live row-count updates while preserving compare/preview behavior.

## 2026-02-10 16:30 UTC

- Added a new Manual Stress/Strain Logger with manual displacement/load input, live load-displacement plotting, and live stress-strain plotting.
- Added geometry-driven conversion (`L0`, diameter) so logged points are saved with derived strain (%) and stress (MPa).
- Added TXT export format for the manual logger with first-row long names and second-row units, plus stress-style file naming via the shared name builder.
- Launcher update: registered the new logger in the Loggers tab.
- Updated manual logger naming workflow: only `Stress` and `Custom` modes, renamed `Annealing` to `Current`, removed stress-load naming fields, added optional sample number and notes.
- Added displacement display mode toggle (`mm` vs `10^-3 mm` points), keyboard entry workflow (Enter-to-focus/load-log), and a scale re-zero offset action for continuity after balance auto-reset.
- Removed the `Sample end` naming field from the manual logger builder and simplified stress naming to optional sample-number tokens (`s1`, `s2`, etc.).
- Improved directory defaults and file-dialog start paths to avoid `microwire_paddle_cache` redirect paths when selecting log folders.
- Added a live 60-second idle indicator (time since last logged load change) and a continuously updating table view of logged points.
- Added micrometer-dial mapping controls in points mode: configurable `Micrometer at d=0` offset plus a live wrapped 0..45 dial readout next to displacement input.
- Updated logger plotting to segment repeated loops automatically into `Loading n` / `Unloading n` traces based on strain direction changes.
- Added a new PyPlot plugin: `Shape Memory Stress/Strain` for manual logger TXT files with segmented load-displacement and stress-strain loop plotting.
- Refined manual logger layout: moved the logged-data table below the plot area and added a persistent plot-view selector (`both graphs` vs `Load vs Displacement only`).
- UI polish: moved the 60-second idle timer into the load-input row as a high-visibility badge and limited numeric input/display fields to at most 3 decimal places.
- Locked manual displacement entry to stepper control (read-only text field): value changes now happen via spinbox arrows only.
- Added a dedicated `Reset d=0` action in Manual Input to zero displacement quickly without clearing logged points.
- Updated the PyPlot `Shape Memory Stress/Strain` plugin to generate separate tabs/graphs for `Load vs Displacement` and `Stress vs Strain` instead of combining both into one figure.
- Added a quick-start option in the `Session not started` popup so users can start logging directly when trying to add a point.
- Corrected micrometer-points scaling in the manual logger from `10^-3 mm` to `10^-2 mm`, including updated axis/input labels (`x10$_{-2}$`).
- Data migration: corrected sample measurements in `sample_data/manual_stress-strain` by multiplying displacement and strain values by 10 to match the updated `10^-2 mm` points scale.
- Added a dual-axis overlay plot-view mode in the manual logger (`Load vs Displacement` on left+bottom, `Stress vs Strain` on right+top) while keeping existing separate-plot modes.
- Added matching layout options to the PyPlot `Shape Memory Stress/Strain` plugin: separate tabs or one dual-axis overlay graph.
- Added Sample Geometry integration with Microwire Data Builder project files (`.pydpj`/`.pypdj`): you can connect a project and auto-fill wire diameter from `d (µm)`, with composition+microwire matching and manual fallback selection.
- Fixed manual logger name-builder layout collapse by enforcing stable minimum field/panel heights so filename metadata inputs stay readable.
- Fixed dual-axis overlay labeling in the manual logger so stress/strain labels and ticks stay on the right/top axes, and removed duplicated toolbar coordinate readouts from twinned axes.
- Manual logger update: added a configurable start-displacement mode (`Start from 0 points` / `Start from 10 points`), with dynamic `Reset d=...` and `Micrometer at d=...` labels tied to the selected start point.
- Manual logger update: when start mode is `10 points`, logging the first point auto-inserts a prior anchor point at `d=0, load=0` for strain alignment while keeping a single visible curve workflow.
- Manual logger update: dual-axis overlay now draws only one segmented load-displacement curve and uses top/right axes only as transformed strain/stress scales.

## 2026-02-09 12:17 UTC

- Launcher `Plotting` recency ordering now uses launcher-only monotonic open-order + timestamp keys, so stale/background recency writes no longer pin unrelated tools at the top.
- Project Explorer no longer shows an empty `Imported Data` root before any workbook is imported; the root appears on first import and is removed again when it becomes empty.
- Shared Graph formatting now includes explicit show/hide checkboxes beside Title, X label, and Y label so labels can be toggled without deleting text.
- Maximized graph subwindow geometry on macOS now compensates MDI frame/title-bar extents, and registered plot tabs now allow canvas shrink-to-fit, reducing bottom clipping/scrollbar artifacts in fullscreen/maximized views.

## 2026-02-09 11:27 UTC

- Shared Graph formatting now supports per-axis value-factor expressions (for example `10^-3`) with optional unit-label reflection, and keeps the action buttons (`Apply current graph`, `Apply all graphs`, `Read from current`) pinned in a fixed footer so they stay visible while scrolling.
- Save Graph now remembers the last selected export format (`PNG`/`PDF`/`SVG`) and reuses it as the default in the next save dialog.
- Project Explorer readability was improved with middle-elided long text, tuned column sizing, alternating rows, and full-text tooltips for truncated entries.
- Current Annealing default graph presentation now matches the shared PyPlot baseline more closely (smaller default canvas/figure profile and standard label styling).
- Crash diagnostics update: macOS hard-abort repros are now confirmed in native crash reports (`~/Library/Logs/DiagnosticReports/Python-*.ips`) as `SIGABRT` via `pyqt6_err_print`/Qt slot exception handling; no corresponding fresh traceback appears in `logs/message_log.txt`.

## 2026-02-09 10:26 UTC

- Removed the redundant `Export PDF…` control from the shared Graph formatting dialog; PDF export remains available via `Save graph…`.
- FMR plotting now supports automatic forward/back sweep field alignment by shifting branches symmetrically toward overlap based on resonance-field offset (applied consistently in PyPlot and Origin export).

## 2026-02-09 10:05 UTC

- Switched shared `Graph formatting` access from a constrained toolbar popover to a separate movable dialog window (Origin-style workflow) so full formatting controls are visible and can stay open while editing graphs.
- Routed toolbar `Graph formatting` section access and on-canvas double-click (title/labels/axes) into that shared dialog, with focus jumping to relevant label or axis-scale controls.

## 2026-02-09 09:52 UTC

- Changed on-canvas double-click routing to use the shared `Graph formatting` controls first: double-clicking title/X/Y labels now opens the shared formatter focused on label fields, and double-clicking axes opens the same shared formatter focused on axis scale controls.
- Kept the old axis/text edit dialogs as a fallback only when shared formatting controls are unavailable (host/plugin compatibility path).

## 2026-02-09 09:33 UTC

- Graph formatting sections in PyPlot now resolve to a single shared `Graph formatting` panel across plugins (plugin menu section discovery now includes shared settings and de-duplicates duplicate section titles).
- Fixed `Save graph…`/`Normalize Y` action state after plugin/project updates so graph actions re-enable whenever plot tabs are present, including after reopening `.pypj` files.
- Normalized default axis-unit label style to square brackets (for example `Temperature [°C]`, `Strain [%]`) during graph registration.
- Shared graph-format apply now runs a layout-fit pass (`tight_layout`) so large font settings keep labels/titles inside the graph canvas area.

## 2026-02-09 08:37 UTC

- DMA Iso-Stress `Graph formatting` now includes explicit tick placement controls for both axes (`Auto`, `By increment`, `By count`) so major tick spacing/count can be set from the plugin panel.
- PyPlot project save/load now persists active-plugin state; DMA Iso-Stress restores plotted graph tabs (not just imported data) along with legend-entry overrides and per-graph formatting when reopening `.pypj` projects.

## 2026-02-08 16:07 UTC

- Added headless GUI smoke tests using `pytest-qt` for launcher/workbench startup and blank-graph creation paths.
- Added deterministic parser fixtures under `tests/fixtures/` for DMA Iso-Stress and VSM temperature scan inputs with expected outputs.
- Updated test configuration to default Qt to offscreen mode in automated/headless runs (`PYTEST_GUI_HEADLESS=0` disables this).
- Dependency update: added `pytest-qt==4.5.0` to the `test` optional dependency set in `pyproject.toml`.

## 2026-02-06 15:05 UTC

- Shared Graph formatting now supports tick-placement control per axis with selectable mode (`Auto`, `By increment`, `By count`) so you can set explicit major-tick spacing or target tick count.
- Shared Graph formatting now also exposes figure dimensions (width/height in inches) plus axes aspect mode (`Auto`, `Equal`, custom Y/X ratio) so graph size/shape can be adjusted directly from the panel.
- Improved direct double-click editing hit detection so title/axis-label edits also trigger when the click lands outside the axes patch bounds (for example, graph titles above the plot area).
- Tuned side-dock auto-resize behavior on large windows/maximize so Project Explorer/Object Manager widths expand more aggressively instead of staying near small startup widths.

## 2026-02-06 13:50 UTC

- Launcher plotting list now enforces recent-first ordering (`last opened`) and refreshes ordering when the launcher regains focus, so tools opened from inside PyPlot are immediately reflected in the list.
- PyPlot primary side docks (Project Explorer/Object Manager) now re-evaluate width targets on main-window resize and scale up on large/maximized windows instead of remaining fixed at small startup widths.

## 2026-02-06 11:05 UTC

- Added Origin-style direct graph edits on Matplotlib canvases: double-click the title/X label/Y label text to rename it in place, and double-click near an axis to open scale/limits controls (linear/log + auto/manual limits).
- Synced direct-edit changes with PyPlot state so Object Manager labels, graph-format controls, and saved tab descriptors stay consistent after in-canvas edits.

## 2026-02-06 10:48 UTC

- DMA Iso-Stress graph formatting now supports direct legend-entry renaming on the current graph (`Edit legend entries…` / `Reset legend entries`), so legend text can be fully customized without changing source data labels.
- The DMA selective copy flow (`Apply selected formatting…`) now includes legend entry text as an independent formatting group and can propagate renamed legend labels to chosen target DMA graphs.

## 2026-02-06 09:44 UTC

- DMA Iso-Stress graph formatting now includes show/hide toggles for Title, X label, and Y label (text remains editable while visibility is controlled independently).
- Added `Apply selected formatting…` in DMA Iso-Stress so you can choose target DMA graph(s) and copy only selected formatting groups (title/x/y labels, line style, font, grid, legend, and axis limits).

## 2026-02-06 09:18 UTC

- Fixed PyPlot graph save/export actions (`Save graph…`, `Export TXT…`, `Open in Matplotlib`) to work with the MDI tab proxy, resolving false “No plot area is available…” dialogs when a graph is open.
- Restored shared Graph formatting behavior for active MDI graphs by fixing current-canvas/current-axes resolution (title/label/tick/scale/legend edits now apply from the toolbar controls as expected).
- Updated MDI single-window layout so a lone visible subwindow fills the available viewport in windowed/fullscreen use, preventing the bottom cropped gray region seen after fullscreen/resize on macOS.

## 2026-02-05 22:49 UTC

- Microwire Data Builder `Videos` section now auto-normalizes legacy tables that are missing core columns (`Composition`, `Draw`, `Piece`, `Microwire`) so Builder startup and project loading no longer crash on old schemas.
- Runtime requirement note: PaddleOCR is now disabled by default on Python 3.13 because of observed native-runtime crashes in microscope OCR/build integration paths. Set `MICROWIRE_ENABLE_PADDLE_OCR_UNSAFE=1` to force-enable, or use Python 3.12 for supported OCR behavior.

## 2026-02-05 22:24 UTC

- FMR plugin now supports lock-in phase rotation with both manual angle input and automatic angle estimation (minimizes detrended Y residual to flatten Y).
- FMR plotting and Origin export now apply the selected phase rotation, label rotated channels as `X'`/`Y'`, and annotate plot titles with the applied phase angle.

## 2026-02-05 22:13 UTC

- Fixed Object Manager tree population so line items and per-axis legends are gathered from every axes in a figure (including multi-axis plots), not just the final axes.
- Graph formatting apply now rebuilds legends from currently visible lines, ensuring hidden series are removed from legend entries.

## 2026-02-05 22:07 UTC

- Added a shared `Graph formatting` panel in PyPlot (works across plugins) with controls for title/X/Y labels, title/label/tick font sizes, tick length/width, line width, marker size, linear/log axis scale, explicit axis limits, and grid/legend visibility.
- Added one-click `Export PDF…` from the shared graph formatting panel; `Save graph…` now supports preferred default extension ordering (`PNG`/`PDF`/`SVG`) based on the requested export type.
- Fixed blank-graph tab activation so manual graph creation uses `setCurrentIndex(...)` with the MDI tab proxy.

## 2026-02-05 21:40 UTC

- Retired `experiments/simple_scripts` and moved VSM temperature scan processing into `plotting.plugins.vsm_temperature_scan.core`, then repointed PyPlot and Data Builder imports to plugin-local modules.
- Data Builder now uses `plotting.plugins.dma_iso_stress.parser.parse_dma_txt` directly (no experiment script dependency).
- Hardened VSM hysteresis plotting/export numeric column coercion so duplicate axis labels no longer trigger `TypeError: arg must be a list, tuple, 1-d array, or Series`.

## 2026-02-05 21:17 UTC

- Object Manager line visibility toggles now resync Matplotlib legends to include only currently visible plotted series.
- `DMA Iso-Stress` adds a Graph formatting section (title/x-y labels, line width, font size, grid/legend, legend location, optional axis limits) with one-click apply to the current graph or all DMA graphs.

## 2026-02-05 20:41 UTC

- Hardened PyPlot Project Explorer/Object Manager activation handlers so exceptions from tree double-click/activation events are logged instead of aborting the app process on macOS.

## 2026-02-05 19:20 UTC

- Fixed `DMA Iso-Stress` startup in PyPlot when Tkinter is unavailable (for example, some macOS Python builds) by moving TXT parsing to a Tk-independent module.

## 2026-02-05 15:27 UTC

- FMR PyPlot now offers a combined X-only plot option that overlays all samples with a per-sample legend.

## 2026-02-04 18:00 UTC

- Current Annealing Logger now supports explicit supply profiles for HMP4030 vs Owon SPE6102, applying per-supply defaults (min start current, voltage limit, reset/channel settings) and restoring per-supply preferences.
- Fixed Current Annealing Logger ramp initialization so max-current caps are no longer reset to 10 mA and start-current minimums are enforced when profiles change or runs begin.
- Initialization command ordering now respects voltage-first supplies (Owon) and refreshes templates at run start so the voltage limit is set before ramping.
- Expanded the README WSL setup steps to make the Linux-only `.venv-wsl` flow explicit.

## 2026-02-04 15:28 UTC

- Current Annealing Logger now syncs ramp settings from the UI at run start/step boundaries, and adds an optional channel selector (set 0 to skip) to avoid HMP-only commands on single-channel supplies.

## 2026-02-04 15:19 UTC

- Current Annealing Logger now offers a “Reset supply on start” toggle and adds a longer post-reset delay to avoid ignored init commands on some supplies.

## 2026-02-04 15:11 UTC

- Current Annealing Logger now warns when Start current is at/above Max current (to avoid no-ramp runs) and surfaces stop reasons via status messaging/dialogs for automatic stops.

## 2026-02-04 12:11 UTC

- Fixed Current Annealing Logger hold-percent updates to avoid division-by-zero crashes when the hold resistance is zero or invalid (now shows N/A).
- Current Annealing Logger now lifts the max-current value when needed so a higher Start current is honored instead of silently clamping back down.
- Clarified WSL virtualenv setup in the README (WSL shell vs PowerShell, proper `.venv-wsl` layout).

## 2026-02-04 10:35 UTC

- Current Annealing Logger now lets you set the voltage limit (default 30 V) so higher-voltage supplies can use their full compliance range.

## 2026-02-04 10:28 UTC

- Current Annealing Logger now includes a Start current setting (default 10 mA) and uses it for initialization/ramp start to support supplies with higher minimum output.

## 2026-02-02 19:31 UTC

- Avoided redundant subwindow maximize state changes so macOS title-bar zooming no longer locks up the PyPlot window.
- Guarded subwindow maximize state propagation to prevent recursive state-change loops when zooming the main window.
- Stopped forcing subwindow maximize state during fullscreen geometry updates to avoid macOS zoom hangs.
- Disabled native QMdiSubWindow maximize handling on macOS to prevent freeze loops when zooming/restoring the main window.
- PyPlot no longer auto-maximizes on macOS at launch to avoid UI stalls before interaction.
- Debounced dock normalization on macOS to avoid layout thrash that can block initial UI interaction.
- Re-enabled the dock switcher on macOS with hover/overlay disabled so side panel tabs return without flashing.
- Added View menu toggles for Project Explorer/Object Manager/Message Log so closed panels can be reopened on macOS.

## 2026-01-29 14:11 UTC

- Data Builder now preserves microwire suffix tokens (for example, `10-5oe`) when grouping annealing records so other-end measurements stay separate.

## 2026-01-23 13:56 UTC

- Videos tab now mirrors Fabrication rows even without video OCR results, and Open video(s) reports when no matching file is available.

## 2026-01-23 13:24 UTC

- Microwire Data Builder Fabrication section now includes an estimated transition temperature column (derived from e/a) plus a glass pull-off field, with notes retained in the table/export flow.

## 2026-01-22 13:22 UTC

- Switched PyInstaller launcher builds to onedir output so startup avoids one-file extraction delays.

## 2026-01-22 09:04 UTC

- Refined initial dock normalization to better enforce Project Explorer/Object Manager widths on first open.
- VSM Temperature Scan now supports combining low/high field runs into a dual-axis plot with magnetization axis labels (including Origin export titles).
- Import Folders now supports selecting multiple directories in one action.

## 2026-01-21 09:22 UTC

- Improved initial dock layout normalization so Project Explorer/Object Manager resize correctly on first open.
- Origin exports now mirror PyPlot line/symbol styles and titles across VSM Temperature Scan, VSM Hysteresis, DMA Iso-Stress, and FMR; VSM Temperature Scan graph titles/names include field labels.

## 2026-01-21 08:44 UTC

- Fixed VSM Temperature Scan Origin exports so all selected datasets plot and section ordering stays consistent.
- Object Manager now lists line items even when legends are present so plots can be toggled.

## 2026-01-14 13:41

- Updated video handling to compute cumulative baseline lengths per draw, and split the temperature column into `Core temperature (°C)` and `Glass temperature (°C)` across builder outputs.

## 2026-01-14 13:05

- Made the Videos section editable with the same fabrication-style fields, added `Video end length (mm)` + derived `Video microwire length (mm)` columns, and applied video overrides to assemble/preview/export outputs.

## 2026-01-14 11:42 UTC

- Fixed Fabrication imported-row separation wiring to avoid load errors.

## 2026-01-13 17:26 UTC

- Fixed a project-load syntax error after wiring the Data menu actions.

## 2026-01-13 17:19 UTC

- Added a Data menu toggle to separate imported Fabrication rows under an "Imported data:" divider.

## 2026-01-13 17:04 UTC

- Import workflow now shows a summary popup, marks projects dirty correctly, and syncs imported samples into the Fabrication section.

## 2026-01-13 16:36 UTC

- Expanded e/a valence mapping (Co, Cu, Ge, Sn) and moved data import to the Data menu with dedupe/visibility controls.
- Imported workbooks now appear in Project Explorer with show/hide/remove support.

## 2026-01-13 16:03 UTC

- Added e/a calculation (Heusler valence convention) to Fabrication and Assemble outputs.
- Added Assemble import workflow for external workbooks with automatic fabrication backfill and data-source tagging.

## 2026-01-13 14:04 UTC

- Assemble database preview now tolerates 3-part microwire keys during sorting.
- Assemble preview falls back to live FMR section groups when payload grouping is missing.

## 2026-01-13 13:02 UTC

- Assemble preview no longer crashes on 3-part microwire keys and now logs preview failures with tracebacks.
- VSM hysteresis processing skips files without field/signal columns and avoids picking mismatched axes for previews.
- Graph visibility dialogs now support group-level hide/show toggles.
- Compare matrix view highlights full rows on selection and skips off-screen graph rendering for better performance.

## 2026-01-12 10:42 UTC

- Fixed VSM hysteresis metrics calculation for duplicate axis columns so PyPlot graphing no longer crashes.
- Normalized axis label matching in the Data Builder VSM previews to avoid picking time columns when field columns exist.

- VSM hysteresis temperature parsing now favors explicit header temperatures over filename tokens to avoid mislabeled graphs.
- Downsampled VSM/DMA/FMR previews to speed up initial section rendering.
- VSM hysteresis axis selection now prefers field columns that cross zero when available to avoid mis-plotted sweeps.

## 2026-01-12 09:28 UTC

- Assemble now keeps zero-valued strain entries instead of dropping them as empty.
- Current annealing now initializes preview settings early to avoid launcher crashes.
- Fixed current annealing graph grouping to avoid unhashable MeasurementRecord errors.
- Fixed another unhashable MeasurementRecord path in annealing graph selection.
- Restored VSM/DMA/FMR previews by falling back to microwire keys when sample columns are hidden.
- Refreshed section column hiding now resets stale hidden indices so graph columns stay visible after refresh.

## 2026-01-09 18:30 UTC

- Ensured Assemble column selection lists every section column (including duplicates), syncs duplicate selections, and fills current-density columns from phase points when needed.
- Normalized Sample-column hiding and improved compare matrix row heights so stacked graph previews stay full size.
- Merged stray single-angle VSM temperature buckets and downgraded empty VSM file parse failures to warnings to reduce log noise.
- Documented updated builder behaviors in `docs/database_builder.md`.

## 2026-01-08 18:20 UTC

- Enabled the Current Annealing plot button to allow plotting and data import without a preselected file list.
- Updated FMR plotting labels to match the Field/X axes convention and carry units when available.
- Improved VSM folder export visibility and default recursion in the GUI so nested folders are preserved.

## 2026-01-08 17:24 UTC

- Added an FMR PyPlot plugin plus a Data Builder FMR section with Field vs X/Y plots and Origin export support.
- VSM Folder Export now keeps the original @@Columns header structure when writing formatted TXT files.

## 2026-01-08 15:33 UTC

- VSM Folder Export now preserves the input folder structure and file names, only swapping the extension to `.txt`.

## 2026-01-08 12:21 UTC

- Current Annealing auto-loads on import so Plot is enabled after data import, and the directional Origin export now writes both directions into a single worksheet with units/comments populated.

## 2026-01-08 12:03 UTC

- VSM Folder Export experiment now shows its dialog when launched from the launcher.

## 2026-01-08 11:43 UTC

- Assemble export now runs in a background worker with a modal progress indicator and refreshes the preview after completion.
- Fixed HTML export invocation for Assemble (no more `bool`-call crash) and preserved export messaging.
- Current density snapshots now refresh before Assemble builds to keep As/Af/Ms/Mf columns in sync.
- DMA previews now fall back to microwire key grouping and legacy Sample/sample columns are cleaned up on load.

## 2026-01-08 10:13 UTC

- Added `experiments/vsm_folder_export.py` to batch-convert VSM hysteresis and temperature scan files into plain TXT tables grouped by sample folder.

## 2026-01-08 09:27 UTC

- Compare now defaults to a “samples as columns” matrix view with selectable field rows and inline graph previews for side-by-side comparisons.
- Current density snapshots now feed Assemble previews/exports so As/Af/Ms/Mf columns appear reliably.
- VSM/DMA sections strip legacy Sample columns on project load, and DMA uses hidden sample keys by default.

## 2026-01-08 08:08 UTC

- Assemble preview now loads VSM/DMA groups without errors, current density data is included again, and Add to compare shows feedback.
- VSM temperature scan/DMA sections now hide the temporary Sample columns immediately when opening a project (no manual refresh required).

## 2026-01-06 19:25 UTC

- Microwire Data Builder now writes unhandled exception traces to `logs/crash_log.txt` to help diagnose pre-log crashes.

## 2026-01-06 19:03 UTC

- Assemble Preview now runs in a background worker so the busy indicator animates and the UI stays responsive during preview builds.

## 2026-01-06 18:22 UTC

- Assemble Preview now shows a busy progress dialog while building the preview dataset.
- Current Annealing Origin exports now populate units/comments rows for worksheet columns.
- DMA iso-stress Origin exports avoid invalid antialias LabTalk calls and include units/comments header rows.
- VSM hysteresis Origin exports now include sample labels in workbook/graph names when available.

## 2026-01-06 17:46 UTC

- Added `docs/database_builder.md` and `docs/origin_output.md` to capture expected Data Builder and Origin export behavior in one place.
- PyPlot now respects the Message Log capture toggle and appends its log output to `logs/message_log.txt` alongside the Data Builder.
- Assemble preview restores VSM/DMA group loading helpers to prevent Preview Database crashes.

## 2026-01-06 17:19 UTC

- Assemble now merges current density + strain detail fields into the combined dataset and exposes all section columns (including graphs) in the column picker, while missing sections log warnings instead of blocking preview/export.
- HTML-only exports no longer force CSV output and can proceed without processing Videos.
- Origin exports for VSM hysteresis/temp scan/DMA now show units/comments rows, and VSM temp scan/DMA detach Origin sessions so the app can close independently.

## 2026-01-06 14:30 UTC

- Assemble now ensures all output columns are available for selection (including VSM/DMA/graph references) and restores VSM/DMA preview loading for Assemble/HTML export.
- VSM hysteresis Origin export now uses short worksheet column names, assigns distinct series colors, and writes angle comments into the comments row; removed invalid antialias LabTalk calls.

## 2026-01-06 13:05 UTC

- Assemble now uses a single Export dialog button (review settings, then export), and the column picker drives which sections are included.
- Graph columns returned to assembled outputs (Matplotlib/Origin + VSM/DMA references) and can be shown inline in Assemble when selected, with graphs hidden by default.
- Message log alerts now highlight the Message Log dock switcher tab on unread errors.
- Compare now accepts multi-row selections even if the table selection model only reports selected indexes.

## 2026-01-06 11:49 UTC

- Assemble now uses a compact Export settings dialog, remembers preview columns/order/sort in `.pydpj`, and adds a column reorder dialog.
- Assemble outputs now include transition temperature columns (As/Af/Ms/Mf) and omit graph reference columns from the assembled table.
- Microwire column sorting now treats draw/piece values numerically for consistent ordering.
- VSM hysteresis Origin exports keep Origin open, and DMA iso-stress now supports Open in Origin exports.

## 2026-01-06 10:01 UTC

- Developer menu now includes a Message Log capture toggle that appends builder log output to `logs/message_log.txt` in the repo.
- Transition temps no longer jumps back to the first graph after picking values, and Assemble column selection now allows per-column deselection.

## 2026-01-06 09:19 UTC

- Added a Transition temps tab that lists VSM temperature scan samples and lets you double-click graphs to capture As/Af/Ms/Mf values (with export).
- Current annealing, VSM temperature scan, and DMA iso-stress sections now include Open in PyPlot/Origin controls; DMA previews also expose these actions.
- DMA iso-stress plots now include sample variants (e.g., s1/s3) in graph titles.

## 2026-01-05 14:00 UTC

- VSM temperature scan and DMA iso-stress sections now preview multiple graphs per sample row (side-by-side thumbnails) so subfolder/file variants stay together.

## 2026-01-05 11:48 UTC

- VSM hysteresis worksheets now bundle all angles for a temperature into one worksheet with XY column pairs (one workbook per graph) in both PyPlot and Origin exports.

## 2026-01-05 10:01 UTC

- VSM hysteresis now prefers explicit header angle/temperature metadata and uses tighter temperature snapping to avoid spurious 25.6->26°C labels.
- VSM hysteresis axes ignore swapped stored selections so Applied Field vs Signal X stays the default, and Origin opens are deferred slightly to let plots finish.
- Suppressed noisy Windows Qt `QWindowsWindow::setGeometry` warnings during startup.

## 2026-01-05 08:35 UTC

- VSM hysteresis angle parsing no longer misreads composition tokens (e.g., “Ga23”), and PyPlot now defaults to applied-field-for-plot + Signal X direction axes to avoid vertical-line plots.
- VSM table rows now store sample IDs in a hidden column so the visible Sample column stays gone after refresh.
- PyPlot stops auto-maximizing on Windows to avoid geometry warnings on startup.

## 2026-01-03 19:10 UTC

- VSM hysteresis plots now force the Y axis to Signal X direction and prioritize applied-field-for-plot columns for sweep mode.
- Builder geometry clamping now avoids redundant fullscreen snap adjustments on Windows to reduce Qt geometry warnings.

## 2026-01-03 18:55 UTC

- VSM hysteresis Open in PyPlot/Origin now loads data and plots automatically; sample variants (e.g., “NG CA”, “no glass”) persist in graph titles.
- VSM hysteresis axes now fall back from stored PyPlot settings when they would yield a near-flat field axis (sweep-mode fix), and sweep folders are parsed as samples.
- VSM graph tables hide the Sample column consistently after refresh.

## 2026-01-03 13:25 UTC

- VSM hysteresis previews now keep full-size plots per group instead of shrinking them, and the layout warnings from tight layout are suppressed.
- Maximized geometry snapping on Windows no longer emits Qt geometry warnings.

## 2026-01-03 11:09 UTC

- VSM hysteresis previews now render multiple graphs side-by-side per microwire and align axis selection with saved PyPlot settings.
- VSM tables hide the redundant Sample column, and the hysteresis section adds row-level Open in PyPlot/Origin shortcuts.

## 2026-01-03 10:31 UTC

- VSM Data Builder now groups hysteresis angles into shared graphs, merges sample sub-variants into a single row, and adds per-graph Open in PyPlot/Origin buttons.
- VSM temperature scans now carry variant labels so subfolder suffixes remain visible in previews and exports.

## 2026-01-02 22:40 UTC

- PyPlot now keeps the Plot action disabled until required data is loaded for plug-ins that need imported data.
- Updating existing Data Builder CSV/Excel exports now adds VSM/DMA graph columns alongside Strain and avoids column insertion index errors.

## 2026-01-02 21:03 UTC

- Fixed the Assemble section startup crash caused by a missing compare-section hookup.

## 2026-01-02 20:34 UTC

- Added Data Builder sections for VSM hysteresis loops, VSM temperature scans, and DMA iso-stress files with per-sample previews and graph galleries.
- Assemble preview now offers VSM/DMA graph buttons, a tabbed preview panel, and HTML exports that embed VSM/DMA previews alongside annealing/microscope assets.
- Added a Compare section that collects selected Assemble rows for side-by-side data/graph review.
- Added a DMA Iso-Stress PyPlot plugin for plotting TA DMA iso-stress TXT files.

## 2026-01-02 15:02 UTC

- Fixed the Assemble column picker crash on Qt builds that require `ItemIsAutoTristate`.

## 2026-01-02 14:32 UTC

- Assemble preview now supports per-section column selection, multi-column sorting, and column reordering that carries into final exports.
- Added a self-contained HTML export with embedded annealing graphs and microscope images (when available) plus interactive row sorting/preview.
- Assemble preview adds an optional side-by-side graph panel and drops the Python console output in favor of the Message Log.

## 2026-01-02 09:45 UTC

- Assemble now uses manually-entered microscope table values when OCR payloads are missing, so the database build runs with hand-entered diameters and linked images.

## 2026-01-02 09:16 UTC

- Microscope refresh now keeps reviewed d/D values locked, clears review highlights when values go missing, and lets Tab/Shift+Tab move between d and D cells.
- Strain selector dropdowns expand to use available screen height and re-focus after saving a row for faster entry.
- Data Builder queues microscope/log updates onto the UI thread, avoids empty concat warnings, and closes active editors before model resets to reduce Qt timer/editor warnings.
- Fullscreen snapping skips redundant geometry updates to avoid Windows setGeometry warnings.

## 2026-01-01 15:39 UTC

- Data Builder fullscreen alignment now accounts for window frames so maximized windows sit flush without a top gap.
- Strain entry form no longer flips to Update after adding a new row, keeping Add entry ready for the next sample.

## 2025-12-31 12:53 UTC

- Data Builder fullscreen snapping now fills the available screen instead of leaving a top gap.
- Current density adds Mf1-Af1 and Mf2-Af2 delta columns alongside the other repeat-measurement deltas.
- Strain section auto-fills d from microscope keys, allows manual weight edits that recompute stress, and labels stress explicitly in the worksheet export.
- Assemble preview adds toggleable annealing graph visibility plus buttons to open the selected 1000 mA/low mA plots on demand.

## 2025-12-31 11:14 UTC

- Data Builder no longer clamps window geometry while maximized/fullscreen, keeping fullscreen sizing intact.
- Microscope manual entries now advance to the next cell on Enter instead of jumping to the table start.
- Current density value picks respect the selected phase column (Af1/Af2/etc.) instead of overwriting As1.

## 2025-12-31 10:33 UTC

- Current density section now captures As1/Af1/Ms1/Mf1 and As2/Af2/Ms2/Mf2 phase points with delta columns for repeat measurements.

## 2025-12-12 11:58 UTC

- Fixed Microscope preview panels occasionally rendering too small by letting the visible preview expand to fill the available space, and corrected Data Builder window geometry clamping so maximizing no longer triggers `QWindowsWindow::setGeometry` warnings or hides the bottom controls.

## 2025-12-12 10:28 UTC

- Microscope tab now debounces preview scaling to avoid the zoom-in effect, hides the unused preview panel completely (no leftover space when on `d`/`D`), advances selection on Enter (`d`→`D`, `D`→next row `d`), and reloads per-cell review state correctly when opening saved projects.

## 2025-12-12 09:34 UTC

- Ensured legacy Reviewed columns are always stripped on load and allowed the microscope preview scroll area to shrink further so bottom controls stay visible when the window is maximized.

## 2025-12-12 08:56 UTC

- Microscope tab removes the missing‑wires list and row‑level Reviewed column, supports per‑cell review (Enter only greens the active `d` or `D` cell), and lowers splitter/preview minimums so fullscreen keeps bottom buttons visible.

## 2025-12-12 08:07 UTC

- Data Builder sections now scan for pending files in the background to keep launch/project load responsive, fixed a microscope table crash on key handling, and reduced preview minimum heights so fullscreen no longer crops the bottom controls.

## 2025-12-11 18:14 UTC

- Microscope table now edits `d`/`D` inline with cell navigation, enter-to-review, and per-cell green/red colouring (reviewed/unreviewed) while rows missing images are highlighted red; previews flip between core/glass based on the active cell, hiding the Reviewed column and reducing reliance on the side inputs.

## 2025-12-11 15:01 UTC

- Microwire microscope tab now auto-resizes columns to their content, stacks high-quality preview images vertically inside a scroll area, allows a narrower table to free space for previews, and relaxes window sizing to prevent bottom controls from being cropped in fullscreen.

## 2025-12-11 14:51 UTC

- Microwire Data Builder microscope table now colours reviewed flags green/red, keeps the Message Log chrome red when errors occur, reserves space for preview images, and tames window sizing/splitter widths to stay within the visible screen.

## 2025-12-11 14:39 UTC

- Microwire Data Builder microscope rows keep selection while applying/clearing overrides, auto-mark overrides as reviewed, focus the `d` input with arrow-key row navigation and comma/dot normalization, and shrink preview panels to stay within the screen.

## 2025-12-11 14:26 UTC

- Microwire Data Builder project loads now show progress and keep the UI responsive while restoring sections, avoiding the Windows “Not Responding” pause when opening saved projects.

## 2025-12-11 08:43 UTC

- Added an optional Notes field to the Current Annealing file name preset, persisting it with other preset fields and appending it to generated log names when provided.
- Matched the Current Annealing plots to the application font so graph text now aligns visually with the rest of the UI.
- Added a Load (MPa) field to the Current Annealing preset so applied load can be captured and included in the default log name.
- Tightened Microwire Data Builder tables so they respect the visible viewport instead of overflowing past the screen or leaving unused right-hand gutters.
- Added a dual-support strain mode with clamp-span input that doubles the effective cross-section for stress calculations and recomputes shortening from the A/B/C geometry.
- Microwire Data Builder now launches maximized, caps tables to the visible area, prompts to save on close when there are changes, and keeps connected folder paths inside saved projects (per-machine paths remain absolute).
- Fixed current annealing previews by handling Matplotlib legend handles safely, so saved projects and refreshed folders render graphs again.
- Assembly tab content now renders correctly instead of appearing blank, and strain offsets persist per calc mode with clamp span disabled for single-span mode.
- Microscope tab can defer OCR: load entries first, then trigger OCR manually with the new button; OCR no longer blocks manual logging.
- Microwire Data Builder now opens at a screen-aware size (no over-wide/short initial window) and caps table widths to the available display.

## 2025-12-04 15:20 UTC

- Reduced current annealing plot text/marker sizes, tightened layout, and suppressed the Matplotlib “figure.max_open_warning” so large batches render without cropped titles or noisy warnings.
- Added a progress dialog while plotting current annealing batches and kept the Project Explorer “Plots” branch expanded by default so new graphs are immediately visible.
- Fixed current annealing Origin exports by wiring in the title formatter used for Matplotlib, restoring Origin export across PyPlot plug-ins.
- Kept fullscreen graphs pinned to the viewport when switching windows, preventing occasional tiny subwindows while fullscreen mode is active.

## 2025-12-04 15:20 UTC

- Reduced current annealing plot text/marker sizes, tightened layout, and suppressed the Matplotlib “figure.max_open_warning” so large batches render without cropped titles or noisy warnings.
- Added a progress dialog while plotting current annealing batches and kept the Project Explorer “Plots” branch expanded by default so new graphs are immediately visible.
- Fixed current annealing Origin exports by wiring in the title formatter used for Matplotlib, restoring Origin export across PyPlot plug-ins.
- Kept fullscreen graphs pinned to the viewport when switching windows, preventing occasional tiny subwindows while fullscreen mode is active.

## 2025-12-03 10:43 UTC
- Forced VSM Temperature Scan Origin plots to keep symbol size at 1 and auto-stack 10 kOe + 50 Oe runs of the same sample onto one graph (10 kOe on the left Y axis, 50 Oe on the right), sharing the same PyPlot tab.
- Synced PyPlot subwindows so toggling any graph/workbook to fullscreen locks every window into fullscreen until one is restored to windowed mode.
- Cascaded new PyPlot subwindows, added a configurable max-visible window cap (Settings → Set max visible windows…), and auto-hide the oldest window when opening a new one from Project Explorer past the limit.
- Kept fullscreen graphs consistent across tab switches (others hidden, active tab maximized), prevented bottom cropping by resizing to the viewport, and added a Project Explorer context menu to remove imported data directly.
- Updated project-save defaults to use `<plugin name> <date>.pypj`, aligned default TXT export names with workbook labels, and documented the fullscreen/save/export rules in pyplot.md.

## 2025-12-03 10:23 UTC
- Fixed VSM Temperature Scan Origin plots by reusing the de-duplicated, temperature-sorted series for XY pairs, explicitly flagging X/Y designations per column, and forcing speed mode off so axes rescale to the true temperature range instead of row indices.
- Renamed VSM Temperature Scan TXT exports to include the sample name, temperature span, and magnetic field in each filename, keeping derivative/smoothed outputs aligned with the new naming.

## 2025-11-28 12:53 UTC
- Hardened the PyPlot MDI subwindow handling so maximizing graph tabs (including VSM Temperature Scan plots) no longer raises errors when Qt toggles window states, and defaulted window layout to side-by-side half-width tiles instead of stacked overlaps.
- Let VSM Temperature Scan canvases grow with the plot window by removing fixed canvas minimums while keeping the half-width default subwindow sizing so plots scale instead of appearing cropped.
- Cleaned VSM Temperature Scan Origin exports by rescaling layers, disabling speed mode, and mirroring the graph title onto the top X axis (tick labels hidden) to keep titles consistent.
- Filled Stress Sensitivity workbooks with units/comments metadata for every column so Origin exports retain the annotated headers.

## 2025-11-26 09:25 UTC

- Stabilized stress sensitivity plotting: enforced larger embedded canvas sizes to stop cropping, kept legend text following line colours through dark-graph toggles, and guarded temperature dependence workbook registration against missing keys.
- Reworked stress sensitivity Origin exports to mirror the PyPlot view (title on the top axis, manual sample labels with tick labels hidden, preserved delta markers), and populated workbook long names/units/comments for all processed columns.
- Documented the Origin export checklist and per-plug-in folder memory defaults so imports/exports remember paths independently.

# Changelog

## 2025-11-25 12:13 UTC
- Scoped import pickers, TXT exports, and graph saves to remember their last-used folders per plug-in, persisting the history separately instead of sharing one global path.
- Guarded PyPlot subwindow creation so Temperature Sensitivity plots no longer crash on Qt6 when QMdiSubWindow lacks `setWidgetResizable`.
- Routed stress/temperature dependence, stress sensitivity, and VSM temperature scan TXT exports through the per-plug-in export folders to keep Origin/TXT workflows using their own directories.
- Restored temperature dependence workbook registration and added stress dependence workbook creation so plots and Origin/TXT exports surface in Project Explorer.
- Defaulted legend text colour to follow plot colours for all plug-ins and persisted legend preferences per plug-in between sessions.

## 2025-11-24 09:28 UTC
- Removed the PyPlot tab bar entirely (MDI subwindows only), locking graph/worksheet aspect ratios with default width at half the viewport, auto-fit on resize, and synchronized maximize/restore across all windows; documented the rules in `docs/pyplot.md` and `AGENTS.md`.
- Added separate “Plot derivatives” and “Plot smoothed derivatives” toggles for VSM Temperature Scan so smoothed d/dT plots/exports can be shown independently of raw derivatives, and ensured 50 Oe traces remain in legends.
- Added VSM Temperature Scan overlay plots (raw + smoothed + smoothed d/dT per segment with legends) and hardened initial dock sizing to reduce the squashed Project Explorer/Object Manager layout.

## 2025-11-24 08:41 UTC
- Hid the PyPlot tab bar while the VSM Temperature Scan plug-in is active (and auto-restored on deactivate), bumping worksheet tabs to a 960×640 minimum so opened workbooks aren’t tiny.
- Added smoothed d/dT plotting/Origin+TXT exports with dedicated workbooks/graphs, and auto-enable derivatives when “Smooth derivatives” is toggled.
- Fixed VSM Temperature Scan legends to include 50 Oe traces, brightened dark-graph labels/legends, and differentiated left/right Y axes (10 kOe vs 50 Oe) with color-coded labels and comments mirrored into Origin.

## 2025-11-21 15:30 UTC
- Split VSM Temperature Scan smoothing controls into signal and derivative sections, applying the derivative-smoothing toggle to both Matplotlib and Origin d/dT plots/exports with separate window settings.
- Kept VSM Temperature Scan colors consistent across raw/smoothed/derivative Origin graphs (including 50 Oe traces) and drive legends from the workbook comments so arrows/sections appear in the Origin legends.
- Preserved workbook comments for every VSM Temperature Scan sheet and aligned the PyPlot plug-in with the new smoothing controls so plot/export buttons rebuild workbooks with the latest smoothing preferences.

## 2025-11-20 14:08 UTC

- Kept VSM Temperature Scan Matplotlib figures open (main/derivative/smoothed) with clear legends and secondary-axis labeling, removing duplicate temperature rows per section before smoothing/derivatives and tightening section-aware legend/comments.
- Aligned TXT/Origin exports for VSM Temperature Scan: raw and smoothed data now share the same long-name/unit/comment headers, derivative/smoothed workbooks only emit when enabled, and Origin graphs/books include the section comments used for legend text.
- Added a PyPlot VSM Temperature Scan plug-in with heating/cooling and smoothing controls that register Origin-ready workbooks (including derivative/smoothed variants) and updated Origin workbook export to honor explicit axis-role strings for XY column pairs.
- Raised the default dock minimum width so Project Explorer/Object Manager aren’t collapsed on launch, and allowed `.VSM-TSCN-Data` imports so VSM temperature scan files can be loaded directly through PyPlot.
- Re-enabled plotting after imports by recognizing plugins that keep data in `_dataset`, refreshed dock layouts post-import so Project Explorer/Object Manager are immediately responsive, and lowered dock minimum width so users can shrink those panes if desired.
- Made the VSM Temperature Scan plug-in auto-load on import, enabled Plot when files are selected even before parsing, and scheduled post-show dock refreshes so Project Explorer/Object Manager respond without a reopen cycle.
- Disabled the dock switcher to avoid startup interaction glitches, refreshed docks on first show, embedded VSM Temperature Scan plots inside PyPlot tabs (with derivative/smoothed views), and registered its workbooks under the Workbooks root instead of Imported Data.
- Re-enabled dock switcher buttons for quick pane toggling, embedded VSM Temperature Scan plots now select tabs safely in all layouts, and duplicate temperatures are averaged instead of dropped so Origin sees de-duped X values without losing data.
- Averaged duplicate temperatures before smoothing/derivative for VSM temperature scans, kept plugin plot tabs registered internally, and ensured dock toggles remain visible while refreshing after show for responsiveness.
- Allowed VSM Temperature Scan imports to succeed even when Tk isn’t available (so the PyPlot plug-in can load outside the Tk UI environment).

## 2025-11-20 10:15 UTC
- Applied 5-point median + 20-point moving-average smoothing before derivative calculations, added an optional smoothed-view plot, ensured derivative legends render (and carry through to Origin graphs/comments), and aligned TXT exports to match Origin/TScan data/derivative workbooks with long names, units, and comments consistent across both.

- Removed the stray `plotting/strain_3d_plot.py` shim and re-exported its helpers from the plug-in package so Strain 3D Plot now lives solely under `plotting.plugins`, with toolbar settings/shortcuts exposed inside PyPlot.
- Added toolbar sections to the Strain 3D Plot plug-in for quick focus/file-picking actions, keeping the embedded widget discoverable when selected from the PyPlot plug-in list.
- Marshalled background load logs and dataset updates onto the Tk UI thread for the simple scripts (including VSM Temperature Scan) to prevent crashes or freezes during data import and window teardown.
- VSM Temperature Scan now keeps heating/cooling segments in their recorded order, plots/derivatives per segment, and ensures its Tk controls/variables stay bound to the main window to avoid “main thread is not in main loop” errors on exit.
- Hardened VSM hysteresis parsing to accept filenames/headers without degree symbols, honour action blocks and angle offsets, and aligned rescaling/export folder suggestions with the reference loops.
- Fixed coercivity/remanence calculations and legend toggling in the VSM plug-in for offscreen test harnesses.
- Switched pytest’s default capture to `--capture=sys` and forced a POSIX temp root so default `pytest` runs succeed under WSL without FileNotFound errors.
- Simplified VSM Temperature Scan legends to one entry per heating/cooling segment per field, kept segment-sensitive derivatives, and ensured Origin/TXT exports create distinct columns per segment (with stable Matplotlib cleanup to avoid Tk shutdown warnings).
- VSM Temperature Scan now honours the VSM-TSCN section markers (0–3) to build four equal segments per field, using first/last temperatures to label heating vs cooling without over-splitting jitter between points.
- Clarified VSM Temperature Scan legends (no “#” suffixes), added derivative legends, marked secondary Y axes, and exported section-aware comments plus derivative workbooks to Origin when enabled.
- Applied 5-point median + 20-point moving-average smoothing before derivative calculations, added an optional smoothed-view plot, and ensured derivative legends render (and carry through to Origin graphs/comments).

## 2025-11-20 09:39 UTC
## 2025-11-20 08:38 UTC
## 2025-11-19 10:15 UTC

- Project Explorer now keeps uniform row heights and suspends repaints while new workbooks are added, eliminating the sluggish scrolling/expanding behavior when large data batches load.
- The readability toggle once again controls legend symbols: proxy handles always include markers, and when “Show symbols” is off the legend now shrinks its handle spacing (and expands again immediately when the toggle is re-enabled).
- Expanded the project-tree suspension logic to cover plot-tab nodes as well, so the Plots/Workbooks sections appear instantly instead of pausing while hundreds of items populate.
- Origin exports now place sample labels at the true sample centers, drop the duplicate “Sample” axis title, and pin the graph title to the top-center of the frame so the output matches the Matplotlib layout.

## 2025-11-14 14:02 UTC

- Restored the native dock widths so Project Explorer/Object Manager start at a readable size, moved the Workbooks tree above Imported Data only when graphs exist, and double-clicking a workbook now opens its first worksheet tab directly.
- Temperature Sensitivity plots now render continuous sweeps with the same marker size as raw points, auto-centered X-axis labels, and lightweight proxy legend handles so hiding/dragging the legend no longer lags while text-only legends collapse without blank space.
- Origin exports share the same symbol-only continuous traces and workbook nodes disappear automatically once every generated workbook is removed, keeping the Project Explorer uncluttered.

## 2025-11-14 13:33 UTC

- Restored the explicit “Plot Temperature Sensitivity” action label, kept it disabled until data loads, and immediately unlocked Export TXT/Open in Origin so importing once again yields ready-to-run plotting and export buttons without the old Generate Workbooks step.
- Retuned the Temperature Sensitivity visuals: Matplotlib now plots continuous sweeps as standalone symbols with an auto-placed legend, padded X limits, and default-sized text, while Origin exports use the same symbol, centered/bold titles, bold 18 pt Sample labels, and re-aligned 2/1 style tick labels.
- Dark graph mode now only inverts nearly-black text, preserving colored legend entries and delta annotations, and the PyPlot window layout/toolbar spacing was tightened so the bottom controls stay visible at native resolutions.
- Plugin-generated workbooks always appear under the Workbooks section (created up front and expanded per build), and the dock switcher keeps pinned panes visible after hovering the Message Log so Project Explorer remains open.

## 2025-11-14 08:24 UTC

- Removed the redundant “Generate workbooks” button, restored the plugin-specific “Plot Temperature Sensitivity/Dependence/…” labels, and re-enabled the Plot action whenever imports exist so a single click now loads, builds workbooks, and plots the graphs for every plug-in.
- Updated the help docs, ideas list, and plug-in prompts to call out the Plot-driven workflow, and clarified `AGENTS.md` so contributors keep running/adding tests until everything is fully functional.
- Reintroduced the native toolbar chrome for every action (Plot, Variables to plot, Format toolbar entries, etc.) so they match the launcher’s Run/Cancel buttons instead of the flat text style.
- Ensured the Plot action only enables once a plug-in has data or imported file selections (preventing the crash when clicking it with no inputs) and added `tests/test_plot_button_state.py` to exercise that state under an offscreen Qt session.
- Marked the PyPlot plug-in pytest module to skip automatically when Qt runs headless/offscreen so CLI test runs no longer crash under WSL without a display.

## 2025-11-13 15:07 UTC

- Temperature Sensitivity now creates one annotated workbook per plotted graph (raw jittered points, mean markers, continuous traces, and annotation positions) while Origin exports center the bold title at the top, hide the numeric X ticks in favor of the custom sample labels, and collapse their staging workbooks after plotting to leave only the graphs visible.
- Stress Sensitivity adopts the same workflow: the plug-in consolidates each graph's processed data into a dedicated workbook with units metadata, the Matplotlib tabs use a sensible minimum canvas size, and the shared core exposes the export table helper so TXT exports and workbooks stay in sync.
- Added a lightweight test environment (`.venv`) with PyQt6/matplotlib/numpy/pandas/tqdm available so the targeted pytest modules (config, filename parsing, current annealing, etc.) can run headlessly; the full suite still aborts in `tests/test_pyplot_plugins.py` because Qt terminates with signal 6 when instantiating the full PyPlot workbench in this headless CI shell.
- Fixed the indentation regression that accidentally nested `update_ui`/Origin-export helpers inside the workbook builder, restoring the Temperature Sensitivity plugin’s toolbar state updates, and wired the PyPlot workbench to update the launcher’s “last used” timestamps so the launcher’s Plotting tab always reflects the most recently opened plugin even if it was launched from inside PyPlot.
- Added a no-op `PyPlotPlugin.update_ui()` implementation so legacy or partially loaded plug-ins never crash the launcher when it refreshes toolbar state before those classes override the method.
- Removed the extra “Generate workbooks” step for Temperature Sensitivity: the toolbar button now hides when the plug-in is active, and each time you click Plot the per-graph workbooks (with populated long-name/units/comments/F(x) rows) are rebuilt in the Project Explorer/Object Manager automatically.

## 2025-11-12 13:32 UTC

- Pinned the dock switchers for the primary panels so Project Explorer/Object Manager remain open even after the hover-collapse logic runs, keeping the “Generate workbooks” button ready for imports and the temperature-sensitivity tab stretched while Origin helpers continue to target the bundled SDK.
- Made the Veusz selftests import the local `veusz-master` checkout so the full pytest run can exercise those suites without a global Veusz install.

## 2025-11-12 12:27 UTC

- Forced the Project Explorer/Object Manager docks to always stay visible when PyPlot starts, kept the Generate workbooks button clickable even before imports so it can summon the data menu, expanded the temperature-sensitivity canvas by stretching/minimum-size the figure, and now the shared Origin helpers punt to the bundled `origin_ext_python/originpro-main` tree before anything else.

- Made the Project Explorer and Object Manager docks show (and stay pinned) whenever PyPlot or any plugin starts, and now the Generate workbooks action opens the import menu/keeps the label so the workflow leads straight into file selection whenever no imports exist.
- Expanded the temperature-sensitivity tab canvas so the Matplotlib plot fills the tab, surfaced the strain_3d helper module for the pytest scaffold, and documented what Veusz and Gnuplot teach us about reusable plotting patterns and dataset plumbing.

- Refined the Temperature Sensitivity Origin export so speed mode is turned off, the title is bold, 22 pt, and centered, the numeric X ticks are hidden in favor of bold 18 pt “2/1”, “2/2”, … labels placed just above the axis, the legend text adopts the plot colors automatically, and each delta annotation is re-added only once higher up so it never stacks on the raw points.

## 2025-11-11 14:54 UTC

- Restored the dock switcher side buttons for Project Explorer, Message Log, and Object Manager while keeping the panels pinned by default, and reverted the toolbar styling to use native Windows/macOS button chrome so clickable items feel familiar again.
- Project Explorer and Object Manager now stay pinned (and their visibility is remembered between restarts); the toolbars use the native "Run"-style chrome for enabled actions while disabled commands present as plain text, and the menu bar was reordered to File → Edit → View → Developer → Help → Data.
- Object Manager accepts extended selections, so the format toolbar can adjust font weight/size/underline across multiple text objects at once.
- Added Matplotlib layout fixes to the Temperature Sensitivity plots (wider plotting area, outside legend, readable tick labels) and documented the PyPlot workflows/plug-ins in `docs/pyplot.md`.
- Limited automatic “Load data” triggers to real user imports (not restored sessions) so old files no longer cause spurious “Skipping …” logs, and ensured plug-in workbooks mark the session dirty for the new close-save prompt.
- Removed the legacy “Show Console”/“Python Console” menu entries and the Python console dock entirely—use the dock buttons for the Message Log instead.
- Temperature Sensitivity now filters selected files before loading, auto-expands the Imported Data tree when workbooks are created, and treats invalid filenames as informational warnings instead of plotting stale data; units are wrapped in brackets so the metadata reads `[°C]`, and the PDF plug-in's file picker now retains its change notifications.

## 2025-11-11 11:55 UTC

- Locked the Project Explorer and Object Manager docks in place by disabling the auto-hide switcher so the side panels stay visible whenever PyPlot opens a plugin window.
- Centralised toolbar state handling so disabled actions are now visibly greyed out, the Load data button only enables once files are imported, and Temperature Sensitivity automatically loads/registers data immediately after import.
- Added a plugin-switch prompt that lets you spawn a new PyPlot window for the selected plugin (with or without the current imports) instead of silently reusing the existing session, preventing plugin-specific workbooks from bleeding across workflows.
- Added save/close safeguards: PyPlot now tracks dirty sessions, prompts to save/discard/cancel on close, and exposes Undo/Redo (with shortcuts) so toolbar and menu states reflect the current history.
- Styled the primary toolbars so enabled buttons show a visible border while disabled entries remain muted, making it obvious which commands are clickable at a glance.
- Fixed Temperature Sensitivity workbook registration (missing `window_module`) and ensured automatic loads only fire after a real import, eliminating phantom “Load data” clicks before importing.
- Tightened the Load data guard so it only enables when real worksheets exist and fixed the Temperature Sensitivity workbook registration crash (missing `window_module`) when clicking Load data without any imports.

## 2025-11-11 10:58 UTC

- Keep the Project Explorer and Object Manager docks pinned and visible whenever a PyPlot workbench or plugin window launches so the supporting tool panels are always available by default.
- Reworked the Load data workflow to depend on imported files, gate the toolbar action until data exists, create workbooks from those sources, and drop the automatic Data‑menu popup so plugins (e.g., Temperature Sensitivity) just consume the selected inputs.
- Verified that the Origin/Open and TXT export helpers still route through the shared workbench APIs so every plugin stays wired to “Open in Origin”, “Export workbooks to Origin”, and “Export TXT…”.

## 2025-11-07 11:00 UTC

- Deleted the `plotting/legacy/` compatibility package now that all downstream imports target `plotting.plugins.*`, and refreshed the migration docs and README to reflect the final layout.

## 2025-11-07 10:45 UTC

## 2025-11-07 10:15 UTC

- Relocated the temperature, stress, current annealing, and VSM plotting implementations into their plugin packages and replaced the legacy modules with deprecation shims so PyPlot and external tooling import the workflows from `plotting.plugins.*` while still supporting the old entry points.
- Pointed downstream helpers, docs, and regression tests at the plugin modules and added import smokes for the compatibility shims, confirming the plugin migration is complete end-to-end.

## 2025-11-06 18:21 UTC

- Kept the launcher’s Experiments tab visible by default and made optional prototypes resilient to import failures, surfacing a dialog when PaddleOCR-VL is missing instead of hiding the entire section.
- Constrained PaddleOCR inputs to ≤2200 px per side (with RGB conversion when needed) before dispatching to PaddleOCR/PaddleOCR-VL so the converter avoids the native segfault triggered by 6–7k px rasterisations while still embedding the original-resolution pages in the output PDF.

## 2025-11-06 18:01 UTC

- Deferred heavy plotting imports in the launcher so the placeholder window appears immediately and the main UI opens faster even on machines missing optional plotting dependencies.

## 2025-11-06 07:47 UTC

- Documented the plugin registry workflow in the README and migration notes and added a regression test that confirms legacy launchers passed via `available_plotters` continue to appear through `ExternalPlotterPlugin`.
- Covered the Microwire Data Builder recent project menu wiring and partial project reloads with UI-focused tests so the blank-start behaviour and new file actions stay stable.
 
## 2025-11-06 07:05 UTC

- Added a shared “Export workbooks to Origin…” toolbar action in PyPlot that reuses the workbench’s worksheet registry to push fully annotated tables into Origin without generating graphs, including Origin-safe naming, column metadata, and axis role assignment for every plugin workflow.
- Introduced a placeholder “Check outliers…” toolbar action so the upcoming outlier analysis flow already has a visible entry point while remaining disabled until worksheets are available.

## 2025-11-06 06:55 UTC

- Documented the outstanding PyPlot migration, Origin export, annealing logger, and Microwire Data Builder follow-up work in `docs/todo/pyplot_migration_todo.md` so the team can track progress across the pending feature requests.

## 2025-11-03 10:49 UTC

- Fixed the Microwire Data Builder annealing section initialisation so the table splitter exists before the base class resizes columns, eliminating the `_table_splitter` AttributeError at launch.
- Deferred the heavy PyPlot and experiments imports until the launcher placeholder is visible, so running `launcher.py` immediately displays a loading window instead of idling on a blank screen.
- Added an OCR debug toggle to the developer menu so optional Microwire tooling can subscribe without attribute errors.
- Unified the launcher titles/icons under "PyPlot Launcher" and drew an inline app icon so both the splash and main window brand consistently.
- Keep the PyPlot splash visible until tools finish loading so the main window appears responsive once it opens.

## 2025-11-03 10:14 UTC

- Made the Microwire Data Builder UI load lazily so PyPlot plugins can import the core library without triggering circular imports, and include the original exception details when the UI dependencies are missing.

## 2025-11-02 16:32 UTC

- Fixed the PyPlot launcher crash by loading plugin assets lazily, correcting the default configuration lookup, and breaking the circular imports that blocked the VSM and stress workflows from initializing.
- Keep the Message Log docked and defer its hover raise with a queued timer so opening it from the dock switcher no longer crashes on macOS.
- Finished migrating the remaining embedded plugins into the `plotting/plugins/` namespace and moved the legacy GUI modules into `plotting/legacy/` shims, so every plugin now runs without touching the old entry points.
- Removed the deprecated top-level packages `plotting.hsw_*`, `plotting.hysteresis_loops`, `plotting.maxion_continuous`, `plotting.pdf_plotter`, and `plotting.strain_3d_plot`; consumers should import from `plotting.plugins.*` while the reference code remains in `plotting/legacy/` for eventual deletion.
- Moved the shared helper implementation into `plotting/shared/toolkit.py` and dropped the legacy `plotting.utils`/`plotting.common` wrappers, updating all imports to the shared namespace.

## 2025-11-02 09:45 UTC

- Shifted the output-directory helpers (`prepare_output_dir`, last-dir tracking, download/sample defaults) into `plotting/shared/paths.py` and relocated the Origin session utilities to `plotting/shared/origin.py`, ensuring plugins share the same infrastructure while graph saving continues to flow through PyPlot’s Save Graph action.
- Finalised the shared helper migration by re-exporting the curated helper set from the new `plotting/shared/` modules (origin, paths, theme, developer, readability), so plugins consume the shared API while the legacy dialogs keep working.
- Removed the legacy Qt entry points for the temperature/stress/current workflows (`*_gui.py` files), since PyPlot now hosts their UI panels directly.

## 2025-11-02 09:18 UTC

- Restored compatibility shims for `plotting.common` and `plotting.shared.utils` so the launcher and existing tooling keep working while the helper modules migrate into `plotting/shared/`.

## 2025-11-02 09:05 UTC

- Removed the per-plugin backend/save toggles from the temperature and stress workflows so they rely on PyPlot’s shared “Save graph…” and “Open in Origin…” actions, simplifying the plugin settings panels and avoiding redundant output directory prompts.

## 2025-11-02 08:45 UTC

- Centralised plugin logging through `PyPlotPlugin._log`, so every PyPlot workflow reports status to the workbench console consistently while trimming duplicated code across the migrated plugins.

## 2025-11-01 22:15 UTC

- Ported every remaining PyPlot workflow into dedicated `plotting/plugins/<name>/..._plugin.py` packages, updating `PyPlotWorkbench` and the smoke test to load the new modules while leaving compatibility exports in the legacy GUIs.
- Renamed each plugin module to a descriptive `*_plugin.py` filename (e.g. `temp_dep_plugin.py`, `current_annealing_plugin.py`) so the tree no longer carries ambiguous `plugin.py` files and refreshed the migration tracker accordingly.

## 2025-11-01 20:48 UTC

- Removed plugin-specific export menus so Temperature Dependence, Stress Dependence, and Stress Sensitivity now lean on the shared “Save graph…” action, and aligned their panels with the streamlined toolbar UI.
- Migrated the Temperature Dependence workflow into `plotting/plugins/temperature_dependence`, with `PyPlotWorkbench` now importing the plugin from its package and tests updated accordingly.

## 2025-11-01 19:17 UTC

- Rebuilt every plugin's toolbar menu so each section uses native controls, renamed the script toolbar and selector to "Plugin", and sorted the plugin picker by last opened to surface recently used workflows first.

## 2025-11-01 16:20 UTC

- Rebuilt the PyPlot script toolbar menus so each button opens its own settings drop-down, keeping plugin controls directly in the toolbar.

## 2025-11-01 15:47 UTC

- Prevented the VSM hysteresis plugin from crashing when the workbench build omits the Matplotlib pop-out action by guarding the legacy normalization helpers.
- Ensured each Temperature Sensitivity toolbar button isolates its own settings group instead of showing the entire panel.

## 2025-11-01 15:20 UTC

- Relocated the VSM hysteresis workbench plugin into `plotting/plugins/` and split the shared plugin base classes so PyPlot loads the script via the new namespace package.
- Pointed the launcher, VSM plotter, and microwire builder UI at `plotting.pyplot.*` modules directly to reduce reliance on the legacy compatibility wrappers.

## 2025-11-01 13:54 UTC

- Repacked the PyPlot workbench into a dedicated package with compatibility wrappers and
  new plugin/shared/legacy namespaces so we can migrate scripts without breaking existing
  imports or launcher integrations.

## 2025-11-01 13:31 UTC

- Fixed the Microwire Data Builder launch crash by merging the table column
  auto-fit sizing into a single helper so PyQt6 no longer raises
  `AttributeError: 'super' object has no attribute '_auto_fit_columns'`.
 
## 2025-11-01 06:42 UTC

- Moved As/Ms editing into the Current density tab, stacking the Matplotlib previews beside the workbook and recalculating densities from the recorded phase points so hover readouts stay available while you tune transitions.
- Trimmed the Current annealing table back to composition plus graph thumbnails and dropped the legacy interactive picker button now that phase changes live in Current density.
- Replaced direct `QtCore.QPointer` usage with a PyQt6-safe weak reference helper so the launcher stops crashing with `AttributeError: module 'PyQt6.QtCore' has no attribute 'QPointer'` at startup.
- Added default-on draggable legends with new controls for symbol visibility, colour following, orientation, and inside/outside placement, plus a navigation toolbar offering zoom, pan, targeted rescale buttons, a bulk rescale dialog, and a dark graph toggle.
- Streamlined Current density review by removing the area column, grouping As/Ms values together, stripping plot legends/titles, brightening the cursor readout, enabling true cell navigation, and allowing graph double-clicks to paste cursor values while keeping the Project Explorer dock from nudging the window off-screen.
- Hooked temperature dependence “Load data” into the workbook registry so imported files populate the Project Explorer automatically.

## 2025-10-31 21:11 UTC

- Hardened the dock switcher resizing logic with guarded Qt pointers so hovering the Message Log no longer risks a crash before any graphs are drawn.
- Removed the blanket “All” graph settings button and filter the drop-down to just the requested section, keeping each toolbar launcher focused on its own controls.
- Enabled the shared Matplotlib pop-out and TXT export flows for every plotting script, hid the temperature sensitivity banner once graphs exist, and bound legend double-clicks to a rich settings dialog.

## 2025-10-31 19:49 UTC

- Split the script toolbar graph controls into section-specific drop-down buttons so each plugin’s major option group opens from its own launcher.
- Defaults the PyPlot window to stack the script toolbar above the other toolbars while keeping them movable.
- Routed temperature sensitivity load notices into the Message Log, clearing the setup banner once plots are generated and avoiding duplicate terminal output.

## 2025-10-31 19:20 UTC

- Folded the graph settings dock into a `Graph settings` drop-down on the script toolbar so every script keeps its configuration controls in one place.
- Aligned the script, action, and format toolbars to a shared height for a consistent top-row layout.

## 2025-10-31 08:45 UTC

- Replaced the PyPlot workbench graph settings dock with a script toolbar that
  hosts the script selector, load data, and generate plot controls while moving
  shared actions to the general toolbars for a cleaner layout.
- Added an "Import data…" action that mirrors the Data menu prompt so users can
  choose files or folders directly from the toolbar.
- Fixed the temperature sensitivity "Load data" crash by using the Qt
  `SingleShotConnection` flag when clearing the Data menu hover state.
 
## 2025-10-31 08:00 UTC

- Restored the temperature sensitivity Load data workflow so it opens the Data menu when no files are imported, then registers the selected workbooks and logs every filename that was loaded.
- Corrected the Plot Temperature Sensitivity action to select the first generated tab via the QMdi proxy so the button no longer crashes.

## 2025-10-31 07:41 UTC

- Added a shared "Save graph…" workflow that offers PNG/PDF/SVG exports and
  reuses the last save directory across PyPlot sessions.
- Registered temperature sensitivity imports as workbooks with Origin-style
  metadata so long names, units, and the object manager stay in sync with the
  generated plots.
- Updated the temperature sensitivity plug-in to reuse imported files, surface
  clearer load/plot actions, and populate graph metadata for the shared toolbar.

## 2025-10-30 19:05 UTC

- Unified the PyPlot "Load data" workflow so plugins reuse imported workbook
  selections, automatically opening the Data menu when nothing is available and
  preserving object manager metadata across scripts.
- Smoothed the dock switcher hover handling to prevent freezes when the side
  panel tabs are moused over, keeping the PyPlot window responsive.
- Display a "Loading PyPlot Launcher…" placeholder instantly so the master
  launcher no longer appears to hang while its tool list initializes.

## 2025-10-30 17:27 UTC

- Fixed PyPlot's import progress loop so files process correctly without
  raising a syntax error and added defensive type checks when embedding
  workbooks, restoring launcher stability.
- Hardened the Microwire builder's Excel exporters against ambiguous column
  indexes and optional worksheet types to keep microscope OCR layouts sizing
  reliably across engines.

## 2025-10-30 16:15 UTC

- Deferred heavyweight plotter imports in the master launcher so the window
  appears immediately while still supporting every plotting script on demand.
- Auto-sized the microscope OCR worksheet splitter and columns so all
  measurements are visible without hand-tuning column widths.
- Added a cancellable progress dialog for PyPlot data imports and widened the
  initial Project Explorer/Object Manager dock layouts to keep the window
  responsive at startup.
 
## 2025-10-30 14:30 UTC

- Removed the Origin Clone prototype and dependency in favour of a built-in
  Python console shared across PyPlot and the Microwire builder, updating the
  launcher help, experiments list, and tests to match.

## 2025-10-30 13:20 UTC

- Treated `pandas.NA`/`numpy.nan` fabrication imports as blanks so previously
  recorded wire lengths stay intact instead of being overwritten by missing
  values, and added regression coverage for the merge behaviour.
 
## 2025-10-30 12:45 UTC

- Tightened the PyPlot loader so "Load data" only proceeds when real files are
  available, prompting the Data menu when nothing is imported instead of
  passing empty directory selections to plotting scripts.

# Changelog

## 2025-10-30 12:35 UTC

- Fixed the builder worker and CLI code paths so manually selected As/Ms transition points persist into assembled worksheets and exports instead of being dropped.
- Narrowed the microscope diameter fallback so the D column only populates once a glass detection is present, keeping interim core values out of the highlights.

## 2025-10-30 12:30 UTC

- Integrated the stress sensitivity workflow into the PyPlot workbench so the
  host toolbar drives Matplotlib generation, Origin export, and new TXT data
  exports without launching the legacy dialog.
- Added reusable TXT export helpers for stress dependence, stress sensitivity,
  and temperature sensitivity datasets and wired them into the PyPlot export
  buttons.
- Documented the PyPlot stress and temperature plotters in the README to call
  out their Matplotlib, Origin, and TXT export capabilities.

## 2025-10-30 12:05 UTC

- Added a Current density tab that derives current densities from microscope diameters and annealing setpoints, with an exportable worksheet view.
- Let the Assemble preview support column drag-reordering and export the visible worksheet with the on-screen column order.
- Reused in-memory annealing groups for current density calculations so large refreshes no longer stall the UI while reading payloads from disk.
- Normalised current imports to auto-detect mA inputs, fixed the annealing axes, and regenerated thumbnails at higher DPI so plots stay sharp.
- Relaxed fabrication workbook header detection so piece spreadsheets from the lab parse instead of leaving the table blank.
- Added As (mA) and Ms (mA) columns with an interactive plot picker so phase transitions can be annotated and exported alongside the graphs.
- Microscope D values stay blank until a glass measurement is parsed, preventing temporary core values from leaking into the table.
- The launcher now opens an instant "Loading Microwire Data Builder..." shell while the full UI initialises so users get immediate feedback instead of waiting on a blank screen.

## 2025-10-30 10:51 UTC

- Kept fabrication piece metadata from being overwritten by blank imports so length values persist in the fabrication grid.
- Allowed As/Ms phase markers to be edited directly in the annealing table and surfaced live cursor readouts on the preview graphs for manual picking.
- Retired the legacy PyPlot data-sources row in favour of the shared Data menu and removed the Origin Clone prototype from the experiments launcher.

## 2025-10-30 10:00 UTC

- Added `requirements-win.txt` so Windows builders can install the Origin automation
  wheels alongside the shared dependency lock before freezing `launcher.exe`; updated the
  README instructions to reference the new file.
## 2025-10-29 09:49 UTC

- Made microscope OCR faster by trimming the resample ceiling, using the lighter PaddleOCR recognition stack, and caching per-image results for reuse across refreshes.
- Added a `Reviewed` flag and "Mark reviewed" / "Clear review" controls to the microscope table so validated rows can be skipped on subsequent passes while keeping their values visible.

## 2025-10-28 09:50 UTC

- Marked the `originpro` automation dependency as Windows-only and regenerated `requirements.txt` so macOS/Linux installs no longer fail on missing Origin wheels.
- Documented the Windows-only `pip install originpro==1.1.14 originext==1.2.5` step in the README to keep Origin export support available on supported hosts.
- Repaired the Microwire builder refresh routine so the Qt UI imports cleanly after installing the standard requirements.
- Added a stop button (with graceful cancellation) to the PaddleOCR-VL PDF converter so long runs can be aborted without killing the process.
- Hardened the PaddleOCR-VL PDF converter error handling so PDFium data-format issues surface actionable guidance instead of raw tracebacks.
- Require the PaddleOCR-VL extras when the VL option is selected instead of silently falling back to classic OCR.
- Added `paddlex[ocr-core]==3.3.5` to the default dependency set so PaddleOCR-VL installs with the rest of the stack.
- Switched the dependency pin to `paddlex[ocr]==3.3.5`, taught the PaddleOCR-VL converter to remember the most recently used folder, and surfaced detailed guidance when the safetensors paddle backend is missing (macOS users must rebuild safetensors from source or disable VL summaries).

## 2025-10-28 09:25 UTC

- Updated the README extras install command to `pip install '.[test]'` so shells like zsh do not glob away the bracketed extra specifier.

## 2025-10-27 20:30 UTC

- Broadened fabrication diameter parsing to recognise additional core/glass
  headings, normalise string fallbacks, and keep d/D ratios capped at three
  decimals so every measurement from the spreadsheets appears without ellipses.
- Reworked microscope OCR token handling to capture bracketed annotations like
  "[1]6.7µm", attach detections to core/glass markers, and reuse the measured
  values even when PaddleOCR splits number/unit tokens.
- Returned the Project Explorer and Message Log to docked side panes by default
  while retaining hover-driven toggling, so they no longer pop out as separate
  windows unless the user chooses to float them.

## 2025-10-27 19:45 UTC

- Capture every fabrication diameter variant by recognising additional header
  patterns, aggregating duplicate readings, and rounding d/D ratios to three
  decimals so the worksheet reflects the full source data.
- Wire the builder logger into the in-app message log and have microscope OCR
  report both successful detections and missing annotations, giving immediate
  feedback when PaddleOCR is unavailable or yields no results.
- Start the Project Explorer and Message Log as hover overlays that list full
  source paths and processed files, keeping the workspace maximised until the
  panels are explicitly pinned.

## 2025-10-27 15:40 UTC

- Clarified the README quick start to pin virtual environment creation to Python 3.13 (3.13.9 baseline) so macOS and Windows installations share the supported interpreter.

## 2025-10-27 14:55 UTC

- Redirected file pickers to the original user home and forced Paddle temp directories into ASCII-safe caches so Windows no longer shows "Location not available" when connecting data folders.
- Moved microscope OCR refresh work onto a background thread so the builder stays responsive and honours cancel requests while images are analysed.
- Added "Save Project" support with `.pydpj` exports that capture section worksheets and manual overrides without embedding device-specific folder paths; wired save and save-as actions into the menu.
- Prevented microscope preprocessing from upscaling images beyond 4000px and ignored `[2]`-prefixed diameters in glass captures to avoid unsupported Paddle scaling.
- Added `experiments/paddleocr_vl_pdf.py` for PaddleOCR-VL powered PDF-to-text conversion and pinned the required `pypdfium2`/`reportlab` dependencies.

## 2025-10-27 12:10 UTC

- Restored the project’s PaddleOCR/PaddlePaddle dependency pins and removed the
  RapidOCR fallback so environments can continue using the upstream Paddle
  models without relying on Tesseract or ONNX binaries.
- Refreshed the PaddleOCR parsing pipeline to consume the new dictionary-based
  results from paddleocr 3.3 and removed the legacy Tesseract code paths from
  both the data builder and the OCR debug tool.
- Forced Paddle’s cache directory on Windows to a root-level ASCII path
  (`C:\microwire_paddle_cache`) so iconv failures from non-ASCII user profiles
  no longer break model downloads; continue purging and rebuilding caches when
  inference files are missing.
- Reverted the debug tool messaging to reference PaddleOCR only.
- Regenerated dependency guidance below to reflect the Paddle-focused stack.

## 2025-10-27 11:20 UTC

- Replaced the PaddleOCR/PaddlePaddle dependency chain with a RapidOCR (ONNX
  runtime) backend that auto-initialises when Paddle is unavailable so Windows
  installs no longer fail on long path extractions; updated the debug tool and
  builder logs to surface the active engine.
- Extended the Tesseract fallback to reuse the RapidOCR ROI flow when the
  binary is missing, keeping microscope diameter extraction functional without
  an external Tesseract install.
- Regenerated `requirements.txt` to drop Paddle-specific pins and add
  `rapidocr-onnxruntime`, ensuring the dependency lock matches the new
  pyproject specification.
- Surfaced detailed OCR initialisation errors so the debug tool and runtime
  logs explain which dependency is missing or failing.

## 2025-10-27 10:20 UTC

- Passed an ASCII-only `home_path` to PaddleOCR so Windows accounts with
  diacritic user names download models into the temporary cache prepared by the
  builder instead of failing to open `inference.json` from `%USERPROFILE%`.
- Added a regression test that asserts the PaddleOCR initialisation kwargs use
  the cache directory and remain ASCII-safe.

## 2025-10-27 09:45 UTC

- Override Paddle cache environment variables even when they are already set
  so Windows installs with diacritic user profiles stop reusing broken
  `%USERPROFILE%` paths and successfully download PaddleOCR/PaddleX models into
  the ASCII-only cache.

## 2025-10-27 09:45 UTC

- Forced PaddleOCR caches to use ASCII-only home directories (overriding HOME/
  USERPROFILE when necessary) so Windows accounts with diacritics no longer
  trigger repeated `inference.json` load failures during model downloads.

## 2025-10-27 09:30 UTC

- Forced PaddleOCR and PaddleX to download models into an ASCII-only cache
  before the library is imported, purging any previous downloads from diacritic
  Windows paths and retrying so the OCR backends initialise cleanly on laptops
  like “Martin Eliáš”.
- Refreshed the README installation guidance to highlight the
  `pip install -r requirements.txt` runtime setup and the follow-up
  `pip install .[test]` extras command so no manual dependency steps are needed
  outside experiments.

## 2025-10-27 07:58 UTC

- Redirected PaddleOCR’s cache into an ASCII-safe temp directory and purge/retry
  when corrupted downloads are detected so Windows accounts with accented names
  no longer break model initialisation.
- Added `pytesseract` to the core dependency set and synced `requirements.txt`
  so non-experiment tools install without extra manual steps.
- Documented the two-step installation flow (`pip install -r requirements.txt`
  then optional `pip install .[test]`) in the README to clarify how to enable
  experiments and the test suite.

## 2025-10-26 23:15 UTC

- Replaced the microscope Tesseract fallback with an HSV-guided ROI scanner
  that upscales the cropped annotation, runs `image_to_data`, and maps the
  result back to the full frame so bracketed `[1]` measurements are captured
  reliably when PaddleOCR misses them.

## 2025-10-26 20:05 UTC

- Added HSV-based red-text detection and a numpy fallback so microscope focus
  crops capture bracketed annotations even when grayscale thresholds miss them,
  improving PaddleOCR hit rates on the sample captures.
- Surfaced PaddleOCR’s raw detection strings per preprocessing variant inside
  the Microscope OCR Debug tool so you can inspect exactly what the engine
  returns before heuristics filter the values.

## 2025-10-26 17:30 UTC

- Fixed PaddleOCR initialisation on macOS/Windows by avoiding the deprecated
  ``show_log`` flag and reporting setup failures through the in-app message log.
- Treated current annealing inputs as milliamperes end-to-end, widened the
  inline worksheet graphs with smaller typography, and removed redundant
  setpoint/sample columns from the export workbook.
- Surfaced every d, D, and d/D value captured in fabrication spreadsheets and
  simplified the Connect Folder control into a single confirmable toggle.

## 2025-10-26 17:28 UTC

- Tuned PaddleOCR initialisation with higher-sensitivity detection defaults and
  added focus-region crops so microscope captures with bracketed micrometer
  overlays consistently yield d/D measurements.
- Upscaled microscope preprocessing to 4K, mapped cropped detections back to
  the source image, and added ROI extraction via OpenCV to reduce the number of
  missed annotations in the fabrication workflow.
- Reworked the Microscope OCR Debug tool’s preview area into a single
  vertically scrolling column, widened the splitter layout, and removed
  horizontal scrolling so it is easier to compare preprocessing variants and
  inspect full-resolution images.

## 2025-10-26 15:32 UTC

- Reworked the Microscope OCR Debug tool with a resizable splitter layout, a
  dedicated output pane, and double-clickable variant previews that open
  full-resolution dialogs so it is easier to compare preprocessing results and
  inspect the source image.
- Tuned the microscope OCR pipeline to upscale captures more aggressively and
  run PaddleOCR on the untouched image before processing variants, emitting a
  debug trace when no text is returned so bracketed micrometer annotations are
  less likely to be missed.

## 2025-10-26 14:45 UTC

- Tuned the microscope fallback OCR to upsample annotations, scan multiple
  cropped regions, and try several Tesseract configurations so `[1]` markers
  reliably produce core and glass diameters when PaddleOCR misses the text.
- Defaulted the Microscope OCR Debug tool to the `base` preprocessing variant
  to simplify one-click experiments while keeping other filters opt-in.

## 2025-10-26 13:48 UTC

- Added live image previews to the Microscope OCR Debug experiment so the
  selected capture and every preprocessing variant render side by side,
  making it easier to compare transforms before running OCR.

## 2025-10-26 12:15 UTC

- Added an image picker and progress bar to the Microscope OCR Debug experiment
  so batches can target specific photos while showing live completion status.

## 2025-10-26 11:32 UTC

- Removed inline microscope thumbnails in the worksheet and promoted the side
  previews to high-resolution, resizable panels so annotations remain legible
  without crowding the table.
- Hid the microscope image columns in the grid and upgraded the preview widgets
  to preserve aspect ratio while scaling smoothly during resizes.
- Updated the Microscope OCR Debug experiment to apply the application theme and
  show its window when launched from the master launcher, restoring its
  usability.

## 2025-10-26 10:08 UTC

- Reworked the microscope worksheet to show a single microwire column with
  inline core/glass previews and matching dual previews in the inspector so
  each row surfaces both images alongside the detected diameters.
- Expanded the PaddleOCR preprocessing set (including a Fourier sharpen pass)
  and tagged every recognised text fragment with its variant for richer debug
  output when microscope OCR struggles.
- Added an "Microscope OCR Debug" experiment that batch-tests the sample
  images across PaddleOCR and Tesseract variants, printing the raw text and
  parsed diameters for each preprocessing strategy.

## 2025-10-26 09:40 UTC

- Ensure the microscope worksheet lists every microwire from current
  annealing, preserving image links via placeholders even when OCR cannot
  extract a diameter so manual review is still possible.
- Log every recognised text fragment in OCR debug mode and align the summary
  counters to ignore placeholder entries, clarifying when PaddleOCR supplied
  usable diameters.

## 2025-10-26 09:00 UTC

- Combine multi-row fabrication headers (e.g., ``d`` on one row and ``(µm)`` on
  the next) so every d, D, and d/D reading appears in the fabrication worksheet
  regardless of merged Excel labels.
- Fallback to parsing plain numeric PaddleOCR output when the unit token is
  missing, allowing microscope images such as ``[1]6.7`` annotations to populate
  core/glass diameters instead of reporting empty OCR results.
- Added regression tests for the multi-row header handling and the new OCR
  fallback to lock in the behaviour for future refactors.

## 2025-10-26 08:55 UTC

- Backfilled multi-row fabrication headers so d/D/ratio columns and resistance
  values populate consistently even when the labels span multiple rows in the
  source spreadsheets.
- Improved microscope OCR preprocessing (higher-resolution colour variants) and
  debug logging so every recognised text line is reported when debugging and
  PaddleOCR can pick up `[1]6.7µm` annotations from the sample captures.
- Added regression coverage for the merged-header path to ensure future
  refactors keep the fabrication diameter parsing intact.

## 2025-10-26 07:03 UTC

- Recognised plain `d`/`D` fabrication headers (and other core/glass hints) so
  every spreadsheet diameter now appears in the fabrication grid with the
  expected three-decimal formatting.
- Fixed microscope OCR token parsing to ignore bracket markers like
  `[1]6.7µm`, allowing PaddleOCR detections to feed both core and glass
  measurements without reporting empty results.
- Added regression coverage that drives the OCR pipeline with stubbed
  PaddleOCR output to lock in the bracketed-diameter behaviour and ensure core
  and glass readings propagate through `_group_microscope_measurements`.

## 2025-10-25 19:34 UTC

- Prevent glass feed and other non-diameter spreadsheet columns from being
  misclassified as d/D readings, ensuring fabrication rows show the true core
  and glass diameters alongside rounded ratios.
- Added regression coverage for the refined diameter mapping so future header
  tweaks keep ignoring non-µm fields and still recognise core/glass dimensions.

## 2025-10-25 19:16 UTC

- Populate the fabrication worksheet with every d, D, d/D, and resistance value
  from the source spreadsheets, round ratios to three decimals, and surface both
  draw and piece workbook paths so "Open source file(s)" launches the paired
  Excel files together.
- Add a Develop → Microscope OCR debug mode that logs PaddleOCR results for
  each microscope image and wire the toggle into the microscope section so
  troubleshooting noisy annotations is easier.
- Widen inline annealing graph columns by using the pixmap size for icon layout
  and stretching the cells, ensuring the embedded plots are fully visible in the
  worksheet tables.

## 2025-10-25 02:45 UTC

- Prevented current-annealing refresh failures by keeping preview pixmaps in
  memory, sanitising legacy tables, and wiring the worksheet grid to render
  cached plots per row instead of pickling Qt objects.
- Fixed PaddleOCR initialisation so macOS installs without the optional
  ``show_log`` flag load successfully and the microscope/video OCR tabs run
  again.
- Tightened fabrication workbook discovery to scan top-level composition
  folders first before descending, reducing needless traversal on large shared
  drives.

## 2025-10-25 01:10 UTC

- Hardened the annealing thumbnail renderer to fall back across all Qt image
  formats so inline graphs render even on builds that omit
  `Format_RGBA8888`.
- Added an “Open source file(s)” action to the mini-database tables, wiring in
  multi-row selection, column sorting, and hidden metadata so users can jump
  from summaries to the original TXT/XLSX assets in one click.
- Pruned fabrication discovery to descend only into composition-matched
  folders before parsing, dramatically reducing the time spent scanning large
  directory trees.
- Surfaced raw video and microscope artefacts through the new source action,
  and embedded video paths in the worksheet so OCR jobs expose their inputs.
- Reworked the strain data form into a single horizontal row of inputs to keep
  the layout consistent with the other tabs.

## 2025-10-24 23:10 UTC

- Added a Stop control to every mini-database section and wired the refresh
  loops to honour cancellation so long-running OCR or spreadsheet scans can be
  halted without closing the builder.
- Fixed the current-annealing thumbnail renderer to use the Qt 6 image format
  APIs, restoring the inline graphs on platforms that previously raised
  `Format_RGBA8888` errors.
- Narrowed fabrication parsing to the compositions found in the current
  annealing dataset, skipping unrelated workbooks (with a message-log note) and
  falling back gracefully when nothing matches.
- Made the assembly preview table visible by default so combined data appears
  in the UI as soon as a preview is generated.

## 2025-10-24 22:15 UTC

- Restored the annealing thumbnails by converting raw measurements to mA before
  plotting and rendering them with the Agg backend so the Message Log hover no
  longer crashes the app and each row shows its paired graphs again.
- Hardened fabrication imports to fall back to explicit Excel engines and skip
  unknown workbooks instead of aborting the refresh when a sheet uses an
  ambiguous format.

## 2025-10-24 19:45 UTC

- Embedded the 1000 mA and low-current plots directly into the current
  annealing worksheet rows so each microwire now previews its measurements in
  the grid instead of a separate pane.
- Let mini-database refreshes queue behind the active section without blocking
  other tabs, keeping the UI responsive while still processing files in order.
- Streamlined section layouts by removing the inline folder list (the Project
  Explorer now owns source management) and maximising the worksheet surface.
- Synced fabrication folders to the videos tab automatically and exposed a
  manual “Start video OCR” trigger so heavy OCR runs only when requested.
- Restored PyPlot-style hover docks for the Project Explorer and Message Log,
  preventing the crash on hover and keeping the console available for
  worksheet previews.

## 2025-10-24 17:30 UTC

- Let Microwire Data Builder sections keep running while the rest of the UI
  stays responsive, queuing additional refreshes until the active one
  completes and logging a clear notice instead of flooding the terminal with
  per-file resistance warnings.
- Added live current-annealing previews with side-by-side 1000 mA and
  low-current Matplotlib plots inside the tab so measurements can be reviewed
  without leaving the app.
- Reworked the builder workspace to match PyPlot’s docked layout: the tabbed
  worksheet area now fills the window, a hover-to-open Project Explorer lists
  connected folders and status per section, the Message Log moved into its own
  dock, and the Assemble tab streams its preview dataframe into a Python
  console before export.

## 2025-10-24 15:05 UTC

- Added progress bars with live time estimates to every Microwire Data Builder
  section so long-running refreshes surface their status instead of appearing to
  hang.
- Let the current annealing tab export a summary worksheet that groups
  microwires, lists the associated setpoints, and embeds 1000 mA / low-current
  plots directly in Excel.
- Taught the Assemble tab to preview the combined database in-app, select which
  sections to include, and build partial exports without forcing unused data.
- Fixed the fabrication tab crash caused by the missing `build_fabrication_index`
  import so spreadsheets can be processed again after the OCR refactor.

## 2025-10-24 13:45 UTC

- Replaced the microwire OCR pipeline with PaddleOCR for both video frame and
  microscope image analysis, retiring the Tesseract dependency while preserving
  diameter detection metadata.
- Added PaddleOCR/PaddlePaddle runtime requirements and bumped the NumPy pin to
  2.3.4 so the new OCR backend installs cleanly on Windows/macOS laptops.

## 2025-10-24 12:15 UTC

- Removed the extra milliamp comment row from converted current annealing logs
  so rewritten files now contain only the standard header followed by numeric
  data, matching the logger output format the user expects.

## 2025-10-24 11:54 UTC

- Hardened the current annealing unit converter so it respects existing
  milliamp headers, only scales logs that declare amperes, and rewrites every
  file to start with `Current (mA)\tVoltage (V)\tResistance (Ohm)` without the
  leading comment marker.

## 2025-10-24 11:26 UTC

- Updated the current annealing unit converter so converted logs now keep a
  single `# Current (mA)\tVoltage (V)\tResistance (Ohm)` header row without the
  extra milliamp marker line, matching the logger output users expect.

## 2025-10-24 11:13 UTC

- Updated the current annealing unit converter to insert the standard
  `Current (mA)\tVoltage (V)\tResistance (Ohm)` column header when legacy logs
  are missing it, so converted files always expose the expected worksheet
  titles alongside the milliamp marker.

## 2025-10-24 10:03 UTC

- Added an adjustable strain offset control to the microwire data builder so
  the strain worksheet now calculates `((M length - A length) / M length + C) *
  100` with a default `C` value of 7 that users can tune, and existing entries
  recompute automatically when the offset changes.

## 2025-10-24 09:44 UTC

- Moved the current annealing mini database tab to the first position so the
  workflow starts with selecting measurement folders before other data types.
- Filtered the fabrication mini database to keep only draws and pieces that
  appear in stored current annealing records, preventing unrelated wires from
  being ingested.

## 2025-10-24 09:31 UTC

- Fixed the microwire data builder launch error by instantiating `MiniDatabaseSection` before its subclasses so imports no longer raise a `NameError`.
- Replaced the strain section with a persistent in-app worksheet that suggests compositions and microwires from processed annealing data, auto-fills diameters, derives mass/strain values, tracks used samples, and exports the curated table to Excel.

## 2025-10-23 16:40 UTC

- Converted the shared Load/Generate/Export buttons into a global PyPlot action
  toolbar so every plotting script reuses the same controls and the Data menu
  now opens directly from the menubar when sources are missing.
- Started the Project Explorer and Object Manager docks in an opened state and
  switched Matplotlib canvases to tabbed MDI view so each plot fills the window
  by default while keeping the dock switcher behaviour.
- Dropped the per-script readability panels, defaulted readability tweaks to
  off across plotting modules, and left log messages to report load counts so
  on-screen instructions stay uncluttered.
- Refreshed the current annealing plotting workflow so multi-file runs create
  full-sized tabs with automatic sizing and the Object Manager tree now lists
  axes and lines for those plots.

## 2025-10-23 13:05 UTC

- Added a format toolbar to PyPlot that tracks the Object Manager selection so
  line/marker styles, colours, and text emphasis can be tweaked directly inside
  the main window.
- Introduced a master toggle for readability settings, letting plotters fall
  back to automatic sizing whenever the new checkbox in the Readability section
  is cleared.
- Synced imported data sources with the Load data workflow, opening the Data
  menu from the menubar and auto-selecting newly imported files so scripts such
  as current annealing can plot and export to Origin without re-entering paths.

## 2025-10-23 09:25 UTC

- Normalised current annealing log headers so continued runs keep the
  `# Current (mA)` line instead of dropping back to bare column names.
- Made "Load data" open the shared Data menu whenever a plotting plugin that
  needs imported files has nothing selected, guiding users to import their
  measurements before retrying.

## 2025-10-23 08:45 UTC

- Added a measurement history dialog to the current annealing logger, pruning
  interim 1 mA samples and persisting the latest three resistance–current plots
  across sessions.
- Retuned the logger’s progress estimator so 30 V projections immediately
  recalibrate the progress bar and time remaining when the ceiling is lower than
  the configured current limit.
- Streamlined the current annealing PyPlot plugin by relying on shared workbench
  actions for saving, Origin export, and data import while keeping only
  annealing-specific settings.
- Expanded PyPlot’s Object Manager tree to list every axis, legend, and line so
  all plotted objects are visible for future editing.

## 2025-10-22 16:45 UTC

- Improved microscope OCR sensitivity by adding red-channel preprocessing for
  PaddleOCR/Tesseract variants and loosening marker/unit heuristics so
  bracketed annotations like `[1]6.7µm` register even when the unit glyph is
  partially missed.
- Updated the Microscope OCR Debug experiment to preview the new red-focused
  variants, keeping its gallery in sync with the runtime pipeline.

## 2025-10-22 13:27 UTC

- Highlight the live voltage readout in red once it exceeds 25 V, fix the
  "To 30 V" status line to use proper symbols, and refresh the associated
  status messages so they render cleanly.
- Rebuilt the current annealing progress tracking to account for partial loops
  and 30 V reversals, ensuring the progress bar and time remaining estimates
  stay accurate through multi-loop runs.

## 2025-10-22 12:45 UTC

- Fixed the PyPlot temperature dependence TXT exporter to use the dedicated
  workflow, preventing KeyErrors when exporting temperature dependence runs.

## 2025-10-22 11:00 UTC

- Added an output-mode toggle to the Microscope OCR Debug tool so you can switch
  between raw strings and `[1]`-tagged d/D values, with previews and summaries
  filtered to the selected preprocessing variants.
- Disabled the automatic Tesseract fallback during debug runs and exposed the
  new `allow_tesseract_fallback` flag on `_extract_microscope_diameters` to keep
  PaddleOCR-only experiments focused on the chosen engine.

## 2025-10-21 15:30 UTC

- Added a Tesseract-backed microscope OCR fallback so bracketed micrometer
  annotations (e.g. `[1]6.7 µm`) populate the builder even when PaddleOCR returns
  no text, and surfaced the captured strings in debug logs.
- Added regression coverage that stubs pytesseract to ensure the fallback keeps
  recording both core and glass diameters in the database worksheet.

## 2025-10-27

- Simplified the annealing worksheet layout by leading with the composition/
  microwire identifiers, widening the graph columns to the full inline plot,
  and slimming the plot typography so the data area fills each cell without
  oversized labels.
- Removed redundant 1000 mA setpoint/sample columns, kept low-current details,
  and stopped re-scaling currents that already arrive in milliamps to keep the
  worksheet aligned with the raw measurements.
- Collected every available d, D, and d/D value (falling back to draw-level
  records when necessary) while trimming the obsolete bistable column so the
  fabrication sheet shows only the context still used downstream.
- Retried PaddleOCR initialisation without the deprecated `show_log` keyword to
  unblock microscope/video OCR on macOS/Windows builds that ship without it.

## 2025-10-26

- Auto-fit every microwire worksheet to its contents, expand the annealing
  previews so each graph column matches the rendered plot width, and shrink the
  inline chart typography (with legends removed) so the visual data dominates
  the row instead of oversized labels.
- Highlight the Message Log dock in red until unread errors are viewed and route
  all section issues through the log handler, making failures impossible to miss
  outside the VS Code terminal.
- Allow PaddleOCR to initialise on builds without the `show_log` flag, warn when
  OCR or Pillow is unavailable, and surface setup guidance directly in the log
  so microscope/video OCR explains what the environment still needs.
- Surface the full fabrication metadata—including winding speed, glass feed,
  underpressure, bistable status, piece turns and combined notes—directly in the
  fabrication worksheet so no spreadsheet context is lost when reviewing rows.

## 2025-10-24

- Taught the current annealing plugin to build PyPlot worksheets during load,
  label increasing/decreasing traces, and show legends so the Object Manager
  lists meaningful series names.
- Ensured the Load data action summons the shared Data menu even when triggered
  from Generate so current annealing runs can grab imported files without
  re-selecting paths.
- Added visibility checkboxes to the Object Manager for Matplotlib lines and
  legends so plots can be toggled directly from the tree.
- Reworked the Microwire Data Builder into a sectioned workflow that stores
  mini-databases per data source, highlights pending files, lets microscope
  measurements be reviewed and overridden, and assembles the final spreadsheet
  from the cached results without rerunning heavy analysis.

## 2025-10-20

- Updated the temperature sensitivity Origin workflow to release generated
  workbooks automatically so the "Open in Origin" button works reliably from
  PyPlot.
- Improved the current annealing logger to remember multi-loop runs, label log
  files with the loop count, keep discarding the initial zero-resistance sample
  even after restarting, write currents in milliamperes with an explicit header,
  and honour loop counts after reversing early at the 30 V limit.
- Added an experiment utility for batch-converting legacy current annealing log
  folders from amperes to milliamperes, and taught it to skip files that
  already store currents in milliamps.
- Embedded the remaining legacy plotting scripts (stress dependence/sensitivity,
  HSW distribution & load compare, Maxion continuous, PDF plotter, hysteresis
  loops, and Strain 3D) as first-class PyPlot plugins with integrated panels,
  including an overhauled HSW distribution dialog with inline file selection.
- Rebuilt stress dependence as a native PyPlot plugin so it now loads data,
  generates Matplotlib tabs, and exports to Origin through the shared
  workbench controls instead of embedding the legacy window.
- Repaired text encoding in PyPlot plugin controls so minus signs, ellipses,
  and degree symbols display correctly again.
- Warn the stress/temperature data logger and current annealing logger when
  composition percentages do not add up to 100 %, while still allowing
  measurements to proceed.

## 2025-10-19

- Bumped pinned dependencies (matplotlib 3.10.7, numpy 2.2.6 (to satisfy opencv-python) , pandas 2.3.3, plotly 6.3.1, psutil 7.1.1, zeroconf 0.148.0, etc.), raised the runtime floor to Python 3.10, and migrated from `PyPDF2` to the actively maintained `pypdf` 6.1.2 via `pip-compile --upgrade`.
- Persisted the VSM window's maximized state and tightened geometry clamping so the workbench stays put and avoids Qt resize warnings when plots render.
- Wired Object Manager checkbox changes through the shared dispatcher so curve visibility, legends, and undo history stay in sync.

## 2025-10-18

- Fixed the Object Manager toggles so hiding a curve updates the plot and any
  field-direction overlays immediately.
- Simplified VSM plot titles to show the formatted sample name (with subscripts
  and sample ratios) alongside the temperature only.
- Normalised sample labels such as `Ni50Fe27Ga23 5-4` so digits render as
  subscripts and the wire number shows as a slash (`Ni50Fe27Ga23 5/4`).
- Prevented a crash triggered by undocking and re-docking the Project Explorer
  by deferring dock rearrangement until Qt finishes the move.
- Streamlined the README and moved detailed release notes into this changelog.
- Added cascade/tile options to the Window menu to manage open graph and
  workbook windows.
- Introduced a manual workbook editor with create/add/delete/reorder column
  capabilities and persistent import folder history when "Keep File Selections"
  is enabled.

- 2025-12-11 12:05 UTC – Updated strain load calc to use target MPa and dual-support area multiplier; clamp span hides in single mode; reduced table padding to eliminate right blank bar; annealing preview legend markers no longer error.

- 2025-12-11 12:12 UTC – Strain load now requires explicit target stress per mode; clamp span hides in single span; moved saved .pydpj to projects/.

- 2025-12-11 12:44 UTC – Fixed annealing preview legend handling; microscope previews larger for higher-res view; strain load now requires explicit target stress per mode.

- 2025-12-11 13:07 UTC – Adjusted microscope preview sizing to avoid overflow; reduced current density plot padding/tick size for compact view.

- 2025-12-11 13:10 UTC – Strain now reports contraction as negative (uses A−M and dual-point current−initial); default C offset set to 0 for both modes.

- 2025-12-11 13:14 UTC – Strain offset now added to both M and A lengths before computing strain (no longer added after ratio).

- 2025-12-11 13:30 UTC – Fixed strain save crash by reindexing missing columns; added New Project action and optional auto-open last project (Settings → Open last project on startup); store last project path.

- 2025-12-11 13:34 UTC – Added compatibility alias for New Project action to prevent missing attribute errors.

- 2025-12-11 13:37 UTC – Added BuilderWindow-level New Project handler and alias to stop launcher AttributeError.
