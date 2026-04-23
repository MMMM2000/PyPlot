# PyPlot Agent Automation Plan

This note captures a full implementation plan for making PyPlot reliably controllable by Codex in a later dedicated worktree.

The goal is not just batch export automation. The goal is a real, inspectable, persistent PyPlot control surface so an agent can:

- open PyPlot and keep it open
- select a plugin
- import files and folders
- trigger plotting and other plugin actions
- inspect loaded state and visible graph tabs
- interact with graph- and workbook-level features
- save projects and export outputs
- recover safely from failures without leaving the app in a confusing state

## Why This Is Needed

Today PyPlot already has a CLI automation entrypoint in `launcher.py`, but it is designed as a batch runner:

- it opens a `PyPlotWorkbench`
- performs import / generate / export actions
- optionally shows the window during the run
- then always closes the window in the automation cleanup path

That behavior is good for screenshots and manifests, but not for live interactive control.

For requests such as:

- "open these files in PyPlot"
- "plot all new current annealing files"
- "open all VSM Co/Cu folders"
- "change graph formatting"
- "save this as a project"

the current model is the wrong abstraction. We need a persistent session model instead of a fire-and-exit model.

## Current State Summary

Relevant observed facts in the current codebase:

- `launcher.py --pyplot-plugin / --pyplot-import / --pyplot-plot` works as a one-shot automation wrapper.
- `_execute_pyplot_automation_request(...)` constructs a real `PyPlotWorkbench`, performs actions, then closes it in `finally`.
- `--pyplot-show-window` means "show the window while automation runs", not "leave the window open after the command finishes".
- Current automation reaches into private workbench methods such as `_import_paths(...)` and `_load_project_from_path(...)`.
- The automation response model is manifest-oriented, not session-oriented.
- There is no supported way to reconnect to a running PyPlot instance and ask it follow-up questions or send another command.
- There is no supported state query API for things like:
  - active plugin
  - imported source paths
  - currently open plots
  - active graph tab
  - whether the plugin is busy
  - what actions are currently available

## Product Goal

PyPlot should support a durable agent-operated workflow with these properties:

1. A single PyPlot session can be launched and kept alive.
2. Later commands can target that same session.
3. Commands are high-level and stable, not coupled to private UI internals.
4. PyPlot can report enough state back that the agent is not operating blind.
5. Failures are visible, typed, and non-destructive.
6. The UI remains the real source of truth; the automation layer should drive the same workbench, not a second hidden implementation.

## Non-Goals

This plan does not require, at least initially:

- full mouse-level remote desktop control
- OCR or image-only guessing of UI state
- automating every custom dialog in phase 1
- embedding a browser-based automation stack
- rewriting plugins around a separate headless backend

The preferred architecture is to expose stable workbench commands first, then expand coverage gradually.

## Desired User Stories

The later implementation should make the following flows possible:

### 1. Open A Live Session

- launch PyPlot with a chosen plugin
- leave the window open
- return a session identifier

Example intent:

- "Open PyPlot with Current Annealing and keep it open."

### 2. Import Into The Existing Session

- connect to a running session
- import one or many files/folders
- report which imports succeeded or failed

Example intent:

- "Import all current annealing files measured in the last month."

### 3. Plot / Generate On Demand

- trigger the active plugin's plot/generate action
- wait until it finishes
- report the created graph/workbook tabs

Example intent:

- "Plot all imported VSM temperature scan folders."

### 4. Inspect Current State

- list open graph tabs
- list imported sources
- identify the active tab
- identify the active plugin

Example intent:

- "What graphs are open right now?"

### 5. Operate Shared Features

- open graph formatting
- apply shared graph options to current/all plots
- save graph images
- export to Origin
- save `.pypj`

Example intent:

- "Set all open VSM loops to `0° and 90° only`, then save the project."

### 6. Close Or Reset Cleanly

- close the session explicitly
- optionally close only tabs/plots
- optionally clear imported state

Example intent:

- "Close the current PyPlot session and discard unsaved changes."

## Proposed Architecture

Use a layered design:

### Layer 1: Stable Workbench Automation API

Add a narrow public automation surface to `PyPlotWorkbench` and plugin wrappers.

This layer should expose methods such as:

- `automation_get_state()`
- `automation_select_plugin(name)`
- `automation_import_paths(paths)`
- `automation_generate()`
- `automation_list_tabs()`
- `automation_activate_tab(tab_id_or_label)`
- `automation_save_project(path)`
- `automation_export_current_plot(path, options)`
- `automation_open_origin()`
- `automation_close_session()`

Important rule:

- the automation layer should call these public methods
- those methods may internally call existing private helpers
- external launchers and bridges should stop touching underscore-prefixed internals directly

This gives us one supported contract even if the UI internals evolve.

### Layer 2: Session Host

Add a session-oriented host that owns one running `PyPlotWorkbench` instance and listens for commands.

Preferred shape:

- one local-only command bridge per running PyPlot process
- commands serialized as JSON
- responses serialized as JSON
- no network exposure beyond localhost / local named pipe equivalent

Reasonable transport choices on Windows:

- localhost TCP socket bound to `127.0.0.1`
- named pipe
- local socket abstraction if already available in Qt/Python

Recommendation:

- start with localhost TCP on a random local port because it is simpler to debug
- keep it local-only
- include a per-session auth token to avoid accidental cross-talk

### Layer 3: CLI Front-End

Keep `launcher.py` as the user-facing entrypoint, but add true session commands.

Example command families:

- `launcher.py --pyplot-session start --plugin "Current Annealing"`
- `launcher.py --pyplot-session send --session <id> --command-file <json>`
- `launcher.py --pyplot-session state --session <id>`
- `launcher.py --pyplot-session close --session <id>`

or equivalently:

- `launcher.py --pyplot-remote-start ...`
- `launcher.py --pyplot-remote-command ...`
- `launcher.py --pyplot-remote-state ...`
- `launcher.py --pyplot-remote-close ...`

The exact naming can be chosen later, but the important product distinction is:

- batch automation remains available
- persistent remote control becomes a separate first-class mode

### Layer 4: Agent-Facing Command Schema

Define a small, stable command schema for the bridge.

Example commands:

- `select_plugin`
- `import_paths`
- `generate`
- `list_state`
- `list_tabs`
- `activate_tab`
- `apply_graph_format`
- `export_plot`
- `save_project`
- `open_origin`
- `close`

Each command should return:

- `status`
- `errors`
- `warnings`
- relevant payload
- optional updated state snapshot

## Phased Implementation Plan

## Phase 1: Make PyPlot Stay Open

### Goal

Turn the existing one-shot CLI into something that can intentionally leave a live PyPlot window open.

### Changes

1. Add a request flag such as:
   - `keep_alive`
   - or `persistent_session`

2. Update `_execute_pyplot_automation_request(...)` so it does not always close the window in `finally`.

3. If `keep_alive` is enabled:
   - do not call `window.close()`
   - do not close extra top-level widgets created by the session
   - do not quit the QApplication
   - return a session descriptor instead of just a batch summary

4. Introduce an explicit lifecycle path for:
   - temporary batch session
   - persistent interactive session

### Deliverable

At the end of phase 1, this should work:

- launch PyPlot from the command line
- import files
- plot them
- leave the window open for the user to inspect manually

### Why Phase 1 Matters

This is the smallest useful improvement and it directly fixes the exact failure seen in this conversation.

## Phase 2: Add Public Automation Methods To `PyPlotWorkbench`

### Goal

Stop depending on private methods from launcher automation code.

### Changes

Add public workbench methods with clear return payloads. Suggested minimum set:

- `automation_select_plugin(name: str) -> dict`
- `automation_import_paths(paths: list[Path]) -> dict`
- `automation_generate() -> dict`
- `automation_get_state() -> dict`
- `automation_list_tabs() -> dict`
- `automation_activate_tab(identifier: str) -> dict`
- `automation_save_project(path: Path) -> dict`

Each method should:

- validate input
- perform the work through the existing UI/backend path
- raise or return structured errors
- keep emitted state predictable

### Deliverable

The launcher no longer needs to call underscore-prefixed helpers for normal PyPlot automation.

## Phase 3: Add A Session Registry

### Goal

Allow a later command to target an existing PyPlot window.

### Changes

Introduce a session registry containing:

- session id
- PID
- transport endpoint
- auth token
- created time
- current project path if any
- current plugin

Possible storage:

- a small JSON session file under a local app-state folder
- cleaned up on normal exit
- stale entries detected if the PID no longer exists

### Required Behaviors

- `start` returns the new session id
- `list` shows active sessions
- `close` closes the selected session
- `state` queries the selected session

### Deliverable

Codex can say "keep using the current PyPlot session" instead of relaunching.

## Phase 4: Add A Local Command Bridge

### Goal

Send commands to the live PyPlot session.

### Changes

Implement a local bridge process inside the PyPlot session.

Core requirements:

- local-only transport
- simple request/response JSON protocol
- one command handled at a time
- safe command queueing on the GUI thread
- response timeouts

Important technical detail:

- bridge handlers must marshal execution onto the Qt main thread before touching UI state

Suggested command envelope:

```json
{
  "request_id": "uuid",
  "token": "session-token",
  "command": "import_paths",
  "payload": {
    "paths": ["G:/My Drive/1 Projects/Praha/current annealing data/..."]
  }
}
```

Suggested response envelope:

```json
{
  "request_id": "uuid",
  "status": "ok",
  "warnings": [],
  "errors": [],
  "result": {
    "imported": [...],
    "skipped": [...]
  },
  "state": {
    "plugin": "Current Annealing",
    "busy": false
  }
}
```

### Deliverable

The agent can drive a running PyPlot window without spawning a new process for each step.

## Phase 5: Add State Introspection

### Goal

Let the agent see enough state to make good decisions.

### Minimum State Payload

- session id
- current plugin
- loaded project path
- imported paths
- whether work is busy/running
- open workbooks
- open graph tabs
- active tab
- visible warnings/errors from last action

### Tab Metadata Should Include

- stable tab id
- user-facing label
- type: graph / worksheet / layout_graph / plugin tab
- plugin owner
- whether active
- whether hidden/closed-but-restorable if applicable

### Why This Matters

Without introspection, the agent cannot safely answer questions like:

- "Did the files load?"
- "How many graphs were created?"
- "Which graph is currently selected?"
- "Did plotting finish?"

## Phase 6: High-Level Domain Commands

### Goal

Make the common real workflows concise and robust.

### Candidate Commands

- `load_current_annealing_batch`
- `load_vsm_temp_scan_batch`
- `load_vsm_hysteresis_batch`
- `plot_all_imported`
- `save_project_with_default_name`
- `export_all_visible_plots`
- `apply_shared_graph_format`
- `tile_windows`
- `cascade_windows`
- `fullscreen_active_graph`

These can be implemented as command aliases over the lower-level API.

### Why They Help

They reduce repetitive agent logic and keep path filtering / plugin activation / plotting rules close to the app.

## Phase 7: Shared Graph Formatting Automation

### Goal

Allow the agent to manipulate graph appearance and export settings, not just load data.

### Scope

Support automation for common shared formatting options:

- title text
- X/Y labels
- font sizes
- figure width/height
- legend visibility and location
- grid on/off
- axis scale mode
- axis limits
- line width
- marker size

### Proposed Interface

Add a structured graph-format payload, for example:

```json
{
  "target": "current" ,
  "options": {
    "title": "Ni54Fe17Ga27Co2 1/2",
    "show_grid": true,
    "legend_visible": true,
    "figure_width_in": 9.0,
    "figure_height_in": 5.4
  }
}
```

### Deliverable

The agent can do meaningful follow-up tasks like:

- "Open all Co/Cu loops and make the graphs presentation-ready"

## Phase 8: Plugin-Specific Extended Control

### Goal

Support important plugin-specific features beyond shared commands.

### Initial Priority Plugins

1. Current Annealing
2. VSM Temperature Scan
3. VSM Hysteresis Loops
4. R vs T

### Examples Of Plugin-Specific Actions

Current Annealing:

- choose plot mode
- group behavior
- exported workbook naming

VSM Temperature Scan:

- combined vs split mode
- derivative toggles
- smoothing options
- field-pair overlay choices

VSM Hysteresis:

- angle visibility
- grouping mode
- preview X-range defaults if relevant

R vs T:

- residual plot toggle
- cycle grouping behavior

### Deliverable

The agent can control the workflows the user actually uses most often, instead of stopping at basic import/plot.

## Phase 9: Robust Error Handling And Recovery

### Goal

Make automation failures understandable and safe.

### Required Behaviors

- distinguish validation errors from runtime failures
- return structured plugin/import/plot errors
- preserve the live session after a recoverable failure
- allow a later `get_state` even after a failed command
- log the failure in the Message Log and the command response

### Important Cases

- one imported path fails, others succeed
- plugin generate raises an exception
- Origin export fails
- graph formatting payload is invalid
- session token is invalid
- session exists but is busy

### Deliverable

The agent can recover instead of restarting PyPlot blindly.

## Phase 10: Test Coverage

### Goal

Protect the automation surface from regressions.

### Test Layers

#### 1. Unit Tests

For:

- command schema validation
- session registry behavior
- stale session cleanup
- state payload structure

#### 2. Integration Tests

For:

- launch persistent session
- select plugin
- import sample files
- generate plots
- query state
- close session

#### 3. Workflow Tests

Focused on real Praha-style tasks:

- recent current annealing batch
- Co/Cu VSM temp scan folders
- Co/Cu VSM hysteresis folders
- open and keep session alive
- save project after plotting

#### 4. Failure Tests

For:

- missing path
- invalid plugin
- duplicate session close
- command against dead session

### Testing Constraint

Tests must continue respecting the repo rule:

- never run verification directly against the user's real `.pypj` or `.pydpj` project files
- use disposable copies and sample data only

## Phase 11: Optional Visual Automation

### Goal

Extend beyond command APIs when a feature is not yet exposed structurally.

### Possible Future Additions

- controlled screenshot capture of the live session
- tab recognition by tab metadata, not OCR
- optional UI-driving helpers for dialogs not yet commandized

### Caution

This should be a later layer, not the foundation. The primary automation path should stay state- and command-based.

## Recommended File / Module Areas

The later implementation will probably touch:

- `launcher.py`
- `plotting/pyplot/app.py`
- `plotting/pyplot/window.py`
- shared plugin base classes used by PyPlot plugins
- selected plugin modules for plugin-specific automation hooks
- tests covering CLI automation and persistent sessions
- `docs/pyplot.md`

Possible new modules to add:

- `plotting/pyplot/automation_api.py`
- `plotting/pyplot/session_host.py`
- `plotting/pyplot/session_registry.py`
- `plotting/pyplot/command_protocol.py`

## Detailed Recommended Order

Implement in this order:

1. Refactor the current automation path so "batch" and "persistent" are distinct modes.
2. Add public workbench automation methods.
3. Add a simple persistent session mode that keeps the window alive.
4. Add a session registry and explicit session ids.
5. Add a local command bridge.
6. Add `get_state` and `list_tabs`.
7. Add `import_paths`, `generate`, `save_project`, and `close`.
8. Add shared graph formatting commands.
9. Add plugin-specific extended commands for Current Annealing and VSM.
10. Add tests for the Praha workflows.
11. Document the supported command surface in `docs/pyplot.md`.

This order gives useful value early while avoiding premature expansion into every plugin detail.

## Suggested CLI Shape

One reasonable user-facing command family could be:

```text
launcher.py --pyplot-session-start --plugin "Current Annealing"
launcher.py --pyplot-session-command --session <id> --json <command.json>
launcher.py --pyplot-session-state --session <id>
launcher.py --pyplot-session-close --session <id>
```

An alternative could be a recipe-like command:

```text
launcher.py --pyplot-remote-start --recipe start.json
launcher.py --pyplot-remote-send --session <id> --recipe command.json
```

Either is fine. The important part is:

- explicit start
- explicit follow-up command
- explicit state query
- explicit close

## Suggested State Schema

Minimum useful state:

```json
{
  "session_id": "uuid",
  "plugin": "Current Annealing",
  "busy": false,
  "project_path": null,
  "imported_paths": [
    "G:/My Drive/1 Projects/Praha/current annealing data/..."
  ],
  "tabs": [
    {
      "id": "tab-1",
      "label": "Ni54Fe17Ga27Co2 1/2",
      "type": "graph",
      "active": true
    }
  ],
  "last_warnings": [],
  "last_errors": []
}
```

## Safety Rules

The automation layer should follow these rules:

- local-only control surface
- random session token required for command submission
- one command at a time per session
- explicit timeout and timeout response
- no silent swallowing of exceptions
- preserve dirty-state awareness for open projects
- do not auto-save unless explicitly asked
- do not auto-close the session after a successful command unless explicitly asked

## Documentation Work To Include Later

When implementing in the new worktree, update `docs/pyplot.md` to explain:

- the difference between batch automation and persistent session automation
- the supported session commands
- how state queries work
- which plugins support extended automation hooks
- what remains unsupported

## Acceptance Criteria

The work should be considered complete for the first meaningful milestone when all of the following are true:

1. Codex can start PyPlot and keep it open.
2. Codex can reconnect to that same session.
3. Codex can import a list of file/folder paths into that session.
4. Codex can trigger plot/generate for the active plugin.
5. Codex can query which tabs were opened.
6. Codex can close the session explicitly.
7. None of those steps require reaching into private methods from outside the workbench automation API.

## Praha Regression Tasks To Use

Use these exact real-world tasks as regression targets in the future worktree:

1. Open one persistent `Current Annealing` session and import all recently measured current annealing files from Praha.
2. Open one persistent `VSM Temperature Scan` session and import all Praha Co/Cu sample folders.
3. Open one persistent `VSM Hysteresis Loops` session and import all Praha Co/Cu sample folders.
4. Confirm the sessions remain open after the automation command returns.
5. Query state and verify the number of imported paths and generated tabs.
6. Save a `.pypj` project from the live session.

If those workflows are reliable, the automation foundation will already be strong enough to cover many practical user requests.

## Important Constraint

Do this work in a new worktree.

The changes will touch launcher behavior, workbench lifecycle, and likely the public control contract for PyPlot. That is exactly the kind of change that should be developed and tested away from the active checkout.
