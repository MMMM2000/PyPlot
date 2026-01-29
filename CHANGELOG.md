# Changelog

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

## 2026-01-08 17:24 UTC

- Added an FMR PyPlot plugin plus a Data Builder FMR section with Field vs X/Y plots and Origin export support.
- VSM Folder Export now keeps the original @@Columns header structure when writing formatted TXT files.

## 2026-01-08 11:43 UTC

- Assemble export now runs in a background worker with a modal progress indicator and refreshes the preview after completion.
- Fixed HTML export invocation for Assemble (no more `bool`-call crash) and preserved export messaging.
- Current density snapshots now refresh before Assemble builds to keep As/Af/Ms/Mf columns in sync.
- DMA previews now fall back to microwire key grouping and legacy Sample/sample columns are cleaned up on load.

## 2026-01-08 12:03 UTC

- VSM Folder Export experiment now shows its dialog when launched from the launcher.

## 2026-01-08 12:21 UTC

- Current Annealing auto-loads on import so Plot is enabled after data import, and the directional Origin export now writes both directions into a single worksheet with units/comments populated.

## 2026-01-08 15:33 UTC

- VSM Folder Export now preserves the input folder structure and file names, only swapping the extension to `.txt`.

## 2026-01-08 09:27 UTC

- Compare now defaults to a “samples as columns” matrix view with selectable field rows and inline graph previews for side-by-side comparisons.
- Current density snapshots now feed Assemble previews/exports so As/Af/Ms/Mf columns appear reliably.
- VSM/DMA sections strip legacy Sample columns on project load, and DMA uses hidden sample keys by default.

## 2026-01-08 10:13 UTC

- Added `experiments/vsm_folder_export.py` to batch-convert VSM hysteresis and temperature scan files into plain TXT tables grouped by sample folder.

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

## 2025-12-12 08:56 UTC

- Microscope tab removes the missing‑wires list and row‑level Reviewed column, supports per‑cell review (Enter only greens the active `d` or `D` cell), and lowers splitter/preview minimums so fullscreen keeps bottom buttons visible.

## 2025-12-12 09:34 UTC

- Ensured legacy Reviewed columns are always stripped on load and allowed the microscope preview scroll area to shrink further so bottom controls stay visible when the window is maximized.

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

## 2025-11-25 12:13 UTC
- Scoped import pickers, TXT exports, and graph saves to remember their last-used folders per plug-in, persisting the history separately instead of sharing one global path.
- Guarded PyPlot subwindow creation so Temperature Sensitivity plots no longer crash on Qt6 when QMdiSubWindow lacks `setWidgetResizable`.
- Routed stress/temperature dependence, stress sensitivity, and VSM temperature scan TXT exports through the per-plug-in export folders to keep Origin/TXT workflows using their own directories.
- Restored temperature dependence workbook registration and added stress dependence workbook creation so plots and Origin/TXT exports surface in Project Explorer.
- Defaulted legend text colour to follow plot colours for all plug-ins and persisted legend preferences per plug-in between sessions.

## 2025-11-21 15:30 UTC
- Split VSM Temperature Scan smoothing controls into signal and derivative sections, applying the derivative-smoothing toggle to both Matplotlib and Origin d/dT plots/exports with separate window settings.
- Kept VSM Temperature Scan colors consistent across raw/smoothed/derivative Origin graphs (including 50 Oe traces) and drive legends from the workbook comments so arrows/sections appear in the Origin legends.
- Preserved workbook comments for every VSM Temperature Scan sheet and aligned the PyPlot plug-in with the new smoothing controls so plot/export buttons rebuild workbooks with the latest smoothing preferences.

## 2025-11-24 08:41 UTC
- Hid the PyPlot tab bar while the VSM Temperature Scan plug-in is active (and auto-restored on deactivate), bumping worksheet tabs to a 960×640 minimum so opened workbooks aren’t tiny.
- Added smoothed d/dT plotting/Origin+TXT exports with dedicated workbooks/graphs, and auto-enable derivatives when “Smooth derivatives” is toggled.
- Fixed VSM Temperature Scan legends to include 50 Oe traces, brightened dark-graph labels/legends, and differentiated left/right Y axes (10 kOe vs 50 Oe) with color-coded labels and comments mirrored into Origin.

## 2025-11-24 09:28 UTC
- Removed the PyPlot tab bar entirely (MDI subwindows only), locking graph/worksheet aspect ratios with default width at half the viewport, auto-fit on resize, and synchronized maximize/restore across all windows; documented the rules in `docs/pyplot.md` and `AGENTS.md`.
- Added separate “Plot derivatives” and “Plot smoothed derivatives” toggles for VSM Temperature Scan so smoothed d/dT plots/exports can be shown independently of raw derivatives, and ensured 50 Oe traces remain in legends.
- Added VSM Temperature Scan overlay plots (raw + smoothed + smoothed d/dT per segment with legends) and hardened initial dock sizing to reduce the squashed Project Explorer/Object Manager layout.

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

## 2025-11-20 08:38 UTC
## 2025-11-20 09:39 UTC
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

# Changelog

## 2025-11-26 09:25 UTC

- Stabilized stress sensitivity plotting: enforced larger embedded canvas sizes to stop cropping, kept legend text following line colours through dark-graph toggles, and guarded temperature dependence workbook registration against missing keys.
- Reworked stress sensitivity Origin exports to mirror the PyPlot view (title on the top axis, manual sample labels with tick labels hidden, preserved delta markers), and populated workbook long names/units/comments for all processed columns.
- Documented the Origin export checklist and per-plug-in folder memory defaults so imports/exports remember paths independently.

# Changelog

## 2025-12-04 15:20 UTC

- Reduced current annealing plot text/marker sizes, tightened layout, and suppressed the Matplotlib “figure.max_open_warning” so large batches render without cropped titles or noisy warnings.
- Added a progress dialog while plotting current annealing batches and kept the Project Explorer “Plots” branch expanded by default so new graphs are immediately visible.
- Fixed current annealing Origin exports by wiring in the title formatter used for Matplotlib, restoring Origin export across PyPlot plug-ins.
- Kept fullscreen graphs pinned to the viewport when switching windows, preventing occasional tiny subwindows while fullscreen mode is active.

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

## 2026-01-08 18:20 UTC

- Enabled the Current Annealing plot button to allow plotting and data import without a preselected file list.
- Updated FMR plotting labels to match the Field/X axes convention and carry units when available.
- Improved VSM folder export visibility and default recursion in the GUI so nested folders are preserved.

## 2026-01-09 18:30 UTC

- Ensured Assemble column selection lists every section column (including duplicates), syncs duplicate selections, and fills current-density columns from phase points when needed.
- Normalized Sample-column hiding and improved compare matrix row heights so stacked graph previews stay full size.
- Merged stray single-angle VSM temperature buckets and downgraded empty VSM file parse failures to warnings to reduce log noise.
- Documented updated builder behaviors in `docs/database_builder.md`.

## 2026-01-14 13:41

- Updated video handling to compute cumulative baseline lengths per draw, and split the temperature column into `Core temperature (°C)` and `Glass temperature (°C)` across builder outputs.

## 2026-01-14 13:05

- Made the Videos section editable with the same fabrication-style fields, added `Video end length (mm)` + derived `Video microwire length (mm)` columns, and applied video overrides to assemble/preview/export outputs.

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
