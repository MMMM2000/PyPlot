# Changelog

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

## 2025-10-26 17:30 UTC

- Fixed PaddleOCR initialisation on macOS/Windows by avoiding the deprecated
  ``show_log`` flag and reporting setup failures through the in-app message log.
- Treated current annealing inputs as milliamperes end-to-end, widened the
  inline worksheet graphs with smaller typography, and removed redundant
  setpoint/sample columns from the export workbook.
- Surfaced every d, D, and d/D value captured in fabrication spreadsheets and
  simplified the Connect Folder control into a single confirmable toggle.

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
