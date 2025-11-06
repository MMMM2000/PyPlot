# Changelog
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
 
## 2025-10-30 10:51 UTC

- Kept fabrication piece metadata from being overwritten by blank imports so length values persist in the fabrication grid.
- Allowed As/Ms phase markers to be edited directly in the annealing table and surfaced live cursor readouts on the preview graphs for manual picking.
- Retired the legacy PyPlot data-sources row in favour of the shared Data menu and removed the Origin Clone prototype from the experiments launcher.

## 2025-10-30 12:35 UTC

- Fixed the builder worker and CLI code paths so manually selected As/Ms transition points persist into assembled worksheets and exports instead of being dropped.
- Narrowed the microscope diameter fallback so the D column only populates once a glass detection is present, keeping interim core values out of the highlights.

## 2025-10-30 12:05 UTC

- Added a Current density tab that derives current densities from microscope diameters and annealing setpoints, with an exportable worksheet view.
- Let the Assemble preview support column drag-reordering and export the visible worksheet with the on-screen column order.
- Reused in-memory annealing groups for current density calculations so large refreshes no longer stall the UI while reading payloads from disk.
- Normalised current imports to auto-detect mA inputs, fixed the annealing axes, and regenerated thumbnails at higher DPI so plots stay sharp.
- Relaxed fabrication workbook header detection so piece spreadsheets from the lab parse instead of leaving the table blank.
- Added As (mA) and Ms (mA) columns with an interactive plot picker so phase transitions can be annotated and exported alongside the graphs.
- Microscope D values stay blank until a glass measurement is parsed, preventing temporary core values from leaking into the table.
- The launcher now opens an instant "Loading Microwire Data Builder..." shell while the full UI initialises so users get immediate feedback instead of waiting on a blank screen.

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

## 2025-10-27 09:30 UTC

- Forced PaddleOCR and PaddleX to download models into an ASCII-only cache
  before the library is imported, purging any previous downloads from diacritic
  Windows paths and retrying so the OCR backends initialise cleanly on laptops
  like “Martin Eliáš”.
- Refreshed the README installation guidance to highlight the
  `pip install -r requirements.txt` runtime setup and the follow-up
  `pip install .[test]` extras command so no manual dependency steps are needed
  outside experiments.

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

## 2025-10-22 16:45 UTC

- Improved microscope OCR sensitivity by adding red-channel preprocessing for
  PaddleOCR/Tesseract variants and loosening marker/unit heuristics so
  bracketed annotations like `[1]6.7µm` register even when the unit glyph is
  partially missed.
- Updated the Microscope OCR Debug experiment to preview the new red-focused
  variants, keeping its gallery in sync with the runtime pipeline.

## 2025-10-26 14:45 UTC

- Tuned the microscope fallback OCR to upsample annotations, scan multiple
  cropped regions, and try several Tesseract configurations so `[1]` markers
  reliably produce core and glass diameters when PaddleOCR misses the text.
- Defaulted the Microscope OCR Debug tool to the `base` preprocessing variant
  to simplify one-click experiments while keeping other filters opt-in.

## 2025-10-27 07:58 UTC

- Redirected PaddleOCR’s cache into an ASCII-safe temp directory and purge/retry
  when corrupted downloads are detected so Windows accounts with accented names
  no longer break model initialisation.
- Added `pytesseract` to the core dependency set and synced `requirements.txt`
  so non-experiment tools install without extra manual steps.
- Documented the two-step installation flow (`pip install -r requirements.txt`
  then optional `pip install .[test]`) in the README to clarify how to enable
  experiments and the test suite.

## 2025-10-30 12:30 UTC

- Integrated the stress sensitivity workflow into the PyPlot workbench so the
  host toolbar drives Matplotlib generation, Origin export, and new TXT data
  exports without launching the legacy dialog.
- Added reusable TXT export helpers for stress dependence, stress sensitivity,
  and temperature sensitivity datasets and wired them into the PyPlot export
  buttons.
- Documented the PyPlot stress and temperature plotters in the README to call
  out their Matplotlib, Origin, and TXT export capabilities.

## 2025-10-27 09:45 UTC

- Forced PaddleOCR caches to use ASCII-only home directories (overriding HOME/
  USERPROFILE when necessary) so Windows accounts with diacritics no longer
  trigger repeated `inference.json` load failures during model downloads.

## 2025-10-22 12:45 UTC

- Fixed the PyPlot temperature dependence TXT exporter to use the dedicated
  workflow, preventing KeyErrors when exporting temperature dependence runs.

## 2025-10-30 12:45 UTC

- Tightened the PyPlot loader so "Load data" only proceeds when real files are
  available, prompting the Data menu when nothing is imported instead of
  passing empty directory selections to plotting scripts.

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
