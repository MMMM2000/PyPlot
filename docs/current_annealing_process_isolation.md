# Current Annealing process isolation

## Scope and dependency

This change is stacked on `codex/tma-control-process-production` because it reuses that branch's bounded, heartbeat-supervised process kernel. It must be rebased onto `main` after the TMA process kernel is merged; it must not be merged independently while that dependency is absent.

The isolated production path applies to automatic Current Annealing recipes using the shared HMP broker. Direct-serial and manual-console modes retain their existing UI-owned behavior for compatibility. The broker still provides cross-application arbitration, while the dedicated controller owns the Current Annealing channel lease, recipe clock, immediate readbacks, safety decisions, and authoritative files.

## Ownership boundary

- UI process: preflight, operator choices, configuration capture, immutable snapshot display, downsampled plotting, and runtime commands.
- controller process: broker channel lease, output configuration, current ramp timing, readback acceptance, contact and voltage-limit decisions, cycle state, UTC-stamped measurements, logs, final metadata, and summaries.
- shared broker: exclusive physical PSU serialization and capacity arbitration between TMA and Current Annealing.

Commands use the existing bounded FIFO channel. Live state uses a latest-value snapshot channel, so UI lag cannot build an unbounded telemetry backlog. Session identity and generation reject stale commands; a parent heartbeat timeout takes the controller through its safe emergency path.

## Incremental migration

1. Introduce the authoritative run-folder writer and deterministic summary generation independently of Qt.
2. Implement and test the process-owned automatic recipe against a deterministic broker fake, including normal completion and an open-circuit start.
3. Route automatic shared-broker runs through the controller and keep direct/manual operation unchanged.
4. Validate UI snapshot rendering and runtime stop/reverse/update commands offscreen.
5. Perform a later campaign-checked hardware comparison. No hardware execution is part of this software implementation.

## Run folder contract

Each run uses a never-reused `<name>_runNN` directory containing:

- `measurement.csv`: elapsed and UTC time, phase, cycle, direction, set/measured current, voltage, resistance, measured power, integrated energy, current density, and readback age;
- `metadata.json` and `recipe.json`: immutable launch context plus final state and stop reason;
- `run_log.txt`: authoritative lifecycle and safety decisions;
- `run_summary.json`, `run_summary.png`, and `run_summary_detail.png`;
- `run_summary_status.json`: summary success/failure without changing the authoritative run outcome.

Legacy flat text files remain readable and direct/manual runs keep their existing output behavior. New isolated runs do not overwrite or silently append to an earlier run.

## Downstream consumers

PyPlot's Current Annealing plugin and Microwire Data Builder accept either the
run directory, its `metadata.json`, or its authoritative `measurement.csv`.
They validate the session schema before treating a CSV as Current Annealing
data and retain the legacy `.txt` and `.dat` import paths. The shared loader
preserves UTC time, phase, cycle, direction, set and measured current, voltage,
resistance, measured power, energy, current density, and readback age when the
columns are present.
