# AC Susceptibility UI Redesign Notes

This note captures the stabilization-campaign audit for the AC susceptibility logger. It is not a release checklist and should not block bench testing of the current branch.

## Current Problems

- The window still inherits a Current Annealing layout and then hides or repurposes many controls. This keeps behavior compatible, but it makes the code hard to reason about and leaves too many inherited state paths alive.
- Hardware setup combines three different concerns: LCR connection, current-source selection, and shared-HMP broker ownership. Operator messages are now clearer, but the panel still mixes configuration, status, and run recovery.
- Output/resume status is more observable now, but long file paths and fallback-status details can dominate the left panel.
- The sweep plan is functional but dense: LCR model, excitation, frequency list, current sweep, zero-current reference, debug logging, baseline, resume, and run buttons all compete in one vertical workflow.
- Long-running resilience is better after the run-status fallback work, but the UI still depends on several modal warnings. This is acceptable for manual use, but automation should keep relying on status sidecars and non-modal logs.

## Keep From The Current Pass

- Separate workflow sections for output, hardware, LCR settings, measurement plan, and recovery.
- Explicit run-status sidecar and local fallback paths.
- Shared-broker channel confirmation instead of silently trusting legacy CH values.
- Sweep-start and baseline-start cleanup that resets the UI if worker setup fails.
- Operator-facing shared-HMP diagnostics shared with Mini DMA and Current Annealing.

## Proposed First-Viewport Layout

The first screen should be a working instrument console, not a form dump:

- Top status band: LCR state, current-source state, broker lease state, output path state, current task, and run progress.
- Left column: required setup only, grouped as `Output`, `LCR`, `Current source`, and `Plan`.
- Right column: plots and live readbacks.
- Bottom band: run actions and recovery actions.

Advanced controls should move behind explicit expanders:

- LCR custom frequency/excitation lists.
- Open/short correction actions and correction state details.
- Shared-HMP broker host/port/channel details.
- Continuous LCR debug JSONL settings.
- Resume diagnostics and status sidecar paths.

## Refactor Plan

1. Extract an `AcWorkflowPanel` builder class that owns only AC-specific widgets. Keep the existing `MainWindow` behavior but stop constructing new AC UI directly inside the inherited Current Annealing frame.
2. Introduce an `AcUiState` dataclass with `lcr_connected`, `psu_backend`, `broker_channel_confirmed`, `broker_owned`, `run_active`, `run_kind`, `output_path`, `status_path`, and `last_error`.
3. Replace scattered label updates with one `_render_ac_ui_state()` method. Event handlers should update state, then render.
4. Move inherited Current Annealing cleanup into a single compatibility shim. The shim should be boring and testable: hide inherited widgets, map inherited sticky buttons, and release inherited serial handles.
5. Convert modal-only warnings into paired behavior: keep the modal for manual users, but always also write the same message into the status label and diagnostic sidecar.
6. Add screenshot tests for the compact panel and expanded-hardware-details panel.

## Deferred Decisions

- Do not remove inheritance until the AC logger has a standalone startup smoke test and the shared plotting/dashboard behavior is either copied cleanly or extracted.
- Do not redesign the sweep data model in the UI refactor. The current TSV/status sidecars are the compatibility surface.
- Do not auto-select a shared-HMP broker channel from legacy settings without confirmation. The COM/CH mistakes have caused enough bench friction that explicit channel confirmation is worth one click.
