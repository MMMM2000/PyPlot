# PyPlot Agent Automation Plan

This note captures the next-step plan for making PyPlot reliably operable by Codex as a persistent desktop tool, not only as a one-shot batch runner.

## Goal

The target workflow is:

- open PyPlot and keep it open
- select a plugin
- import files or folders
- trigger plotting and shared actions
- inspect loaded state and open graph tabs
- save projects and exports
- continue issuing follow-up commands against the same running session

## Current Limitation

PyPlot already supports recipe-style automation through `launcher.py`, but that path is still batch-oriented:

- create a `PyPlotWorkbench`
- perform the requested import / plot / export steps
- write a manifest or outputs
- close the window during cleanup

That works for unattended jobs, but not for requests like:

- "open these folders in PyPlot"
- "switch to Current Annealing and plot all imported files"
- "show me what graphs are open"
- "apply graph formatting to the current tab"
- "save this session as a `.pypj`"

The missing piece is a durable session model with a stable command surface.

## Required Architecture

### 1. Public automation API on `PyPlotWorkbench`

Add a supported automation layer that hides private UI internals behind stable methods such as:

- `automation_get_state()`
- `automation_select_plugin(name)`
- `automation_import_paths(paths)`
- `automation_generate()`
- `automation_list_tabs()`
- `automation_activate_tab(tab_id_or_label)`
- `automation_export_current_plot(path, options)`
- `automation_open_origin()`
- `automation_save_project(path)`
- `automation_close_session()`

Important rule:

- launchers and bridges should stop reaching directly into underscore-prefixed helpers
- those helpers can still be used internally behind the new public methods

### 2. Session host for a live workbench

Add a lightweight local-only bridge owned by the running PyPlot process.

Recommended first version:

- localhost-only TCP server on `127.0.0.1`
- random available port
- per-session auth token
- JSON commands and JSON responses

Why this shape:

- easy to debug on Windows
- works well with the current Python/Qt stack
- good enough for a local Codex-controlled workflow

### 3. Session lifecycle in `launcher.py`

Keep the current recipe automation, but split it clearly from persistent control.

Suggested command families:

- start a live PyPlot session
- send a command to an existing session
- inspect current session state
- close a session explicitly

The key design point is that "show the window" and "keep the session alive" become separate concepts.

### 4. Agent-readable state model

The state response should include enough information that Codex is not operating blind:

- active plugin
- imported source paths
- whether the plugin is currently busy
- open graph/workbook tabs
- active tab
- actions currently available
- current project path, if any

This is what enables requests like "what is open right now?" or "plot only the newly imported files" without relying on screenshots or guesswork.

## Command Scope

Phase 1 should focus on high-value shared actions:

- select plugin
- import files/folders
- generate plots
- list/activate tabs
- save graph
- export TXT
- open in Origin
- export workbooks to Origin
- save project

Phase 2 can add richer graph operations:

- apply graph formatting presets
- create graphs and figures
- refresh or clone figures
- compose graphs
- query visible line labels and axis titles

Phase 3 can add plugin-specific commands where the shared layer is not enough:

- VSM Temperature Scan option toggles
- R vs T residual plotting
- Current Annealing batch-specific helpers
- Builder-side automation later, separately

## Safety Rules

The automation surface should behave like a careful operator:

- reject commands cleanly when a plugin is busy
- return typed errors instead of silently doing nothing
- preserve the live UI as the source of truth
- avoid destructive actions without explicit request
- keep a clear distinction between "no-op because unavailable" and "failure"

## Testing Plan

### Unit tests

- workbench automation methods return stable JSON-safe state
- plugin selection and import commands route correctly
- session command parser validates malformed input safely

### Integration tests

- start a live session, send multiple commands, confirm the session stays alive
- import sample data, generate plots, inspect returned tab state
- export to Origin through the automation layer and verify returned artifacts/state

### Real workflow checks

Use the exact workflows we care about most:

- Current Annealing import -> plot -> Origin export
- R vs T import -> plot -> save project
- VSM Temperature Scan import -> generate -> inspect open tabs

## Suggested Rollout

1. Split current recipe automation into `batch` and `session` paths.
2. Add the workbench public automation API.
3. Add the local session host and session registry.
4. Add CLI commands for `start`, `send`, `state`, and `close`.
5. Cover the Current Annealing, R vs T, and VSM Temperature Scan happy paths first.
6. Expand shared graph/figure operations after the core session loop is stable.

## Immediate Starting Point

When we start implementing this in a dedicated automation pass, the first concrete step should be:

- separate "run automation recipe and close" from "launch a persistent PyPlot session and leave it open"

That split is the foundation for everything else.
