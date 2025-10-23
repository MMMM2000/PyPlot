# Changelog

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

## 2025-10-22 13:27 UTC

- Highlight the live voltage readout in red once it exceeds 25 V, fix the
  "To 30 V" status line to use proper symbols, and refresh the associated
  status messages so they render cleanly.
- Rebuilt the current annealing progress tracking to account for partial loops
  and 30 V reversals, ensuring the progress bar and time remaining estimates
  stay accurate through multi-loop runs.

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
