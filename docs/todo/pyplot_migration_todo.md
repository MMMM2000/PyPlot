# PyPlot Migration and Feature Backlog

## Overview
This document captures follow-up work required to complete the PyPlot plugin migration and related feature requests raised on 2025-11-06. Each task block includes expected deliverables, affected components, and validation notes so work can be scheduled and tracked.

## Tasks

### 1. Complete PyPlot Plugin Migration
- Audit remaining plugins under `plotting/plugins/` to ensure they register through the new loader interface.
- Remove or refactor legacy plugin hooks in `launcher.py` and any compatibility shims left in `plotting/__init__.py`.
- Exercise each plugin via automated smoke scripts once migration code paths are unified.
- Update developer docs to describe the final plugin API.
- 2025-11-07: Converted every plugin to the shared registry decorator, updated the launcher to consume the dynamic catalog, and added smoke coverage that instantiates each registered plugin inside `PyPlotWorkbench`.
- 2025-11-07: Documented the registry auto-discovery flow in `README.md` and `docs/pyplot_migration.md`, and added a regression test that ensures legacy launchers still appear via `available_plotters`.
- 2025-11-07: Moved remaining plotting script implementations into their plugin packages and replaced the legacy modules with deprecation shims that forward to the new locations.

### 2. PyPlot "Export Workbooks to Origin"
- Add a second action next to "Open in Origin" that reuses the workbook assembly pipeline but skips plot export calls.
- Ensure shared preprocessing (long names, units, comments, axis assignments, workbook/worksheet naming) is factored into a reusable helper callable by every plugin.
- Provide telemetry/logging so plugin authors can confirm the export completes even without plotting output.
- Write regression tests verifying worksheets arrive in Origin without charts when the new action is used.
- 2025-11-06: Added a shared "Export workbooks to Origin…" action to PyPlot that pushes the current workbench worksheets into Origin with sanitized names and column metadata while logging successes and failures.
- 2025-11-07: Added a fake Origin harness test to verify worksheet metadata, axis roles, and Origin LabTalk commands are emitted without triggering the real COM bridge.

### 3. PyPlot "Check Outliers" Button
- Add a disabled placeholder button to the PyPlot toolbar/menu labelled "Check outliers…".
- Wire the button into the action registry so future implementations can attach logic without UI refactoring.
- Confirm the button renders consistently for all plugin contexts.
- 2025-11-06: Added the placeholder "Check outliers…" toolbar action that becomes enabled when worksheets are available and displays a "coming soon" message.

### 4. Annealing Logger Availability
- Reproduce launch flows for both annealing loggers (TTY v0/v1) to confirm entry points are still discoverable.
- Inspect recent refactors for missing imports or renamed resources preventing launch.
- Add automated import smoke tests covering both logger modules.
- Document any runtime dependencies now required for the loggers.
- 2025-11-07: Restored the package-level `main` re-export for the current annealing logger and extended the import smoke test suite to cover it.

### 5. Data Builder Improvements
- Default to an empty workbook list on fresh launch while preserving session data inside `.pydpj` projects.
- Introduce "Open Project" and "Recent Projects" entries in the Data Builder "File" menu mirroring PyPlot's UX.
- Constrain initial window size to sensible dimensions (< 1920×1080) and persist user-resized geometry.
- Optimize microscope OCR execution time; consider batching operations or early image classification.
- Pre-populate workbook and preview panes before OCR to surface missing "d"/"D" imagery.
- Fix ingestion when more than two files exist per microwire so all role variants (core/glass/extra) are detected.
- 2025-11-07: Builder now launches with blank sections, supports project open/recent menus, clamps to the active screen, pre-populates microscope image previews (including extra files), reports missing d/D values and image coverage, and limits OCR passes to pending files.
- 2025-11-07: Added tests covering the recent project menu lifecycle and project loading with partial section payloads to guard the new UI hooks.

## Validation & Tracking
- Track completion status in this file and reference commits/PRs as tasks close.
- Update the CHANGELOG once individual features ship to end users.

