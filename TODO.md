# TODO

## Planned
- Fix initial Project Explorer/Object Manager layout so panes render correctly without reopening; ensure status-bar cursor readout is fully visible.
- Make the shared Zoom/Pan/Rescale toolbar functional across all plugins/canvases.
- Persist “Dark graphs” preference between sessions; monitor the new per-plugin import/export folder memory for any regressions.
- Current density section: add As1/Af1/Ms1/Mf1 and As2/Af2/Ms2/Mf2 fields with delta columns for repeat measurements (including Mf-Af deltas).
- VSM Temperature Scan: finalize legends (arrows only; red/blue, orange/green), allow smoothing on derivatives, persist plot options, support per-series hide/show from Object Manager, and embed plots at usable sizes.
- VSM Temperature Scan Origin export: one workbook per sample with Data/Smoothed/Derivative sheets, comments matching legends, consistent colors/anti-aliasing (no speed mode), unclipped axis labels, better graph names/titles, and symbol size set to 1.
- Current Annealing: enlarge embedded plots; ensure legend symbols render with text color following line color.
- Add bottom/status cursor readout shared across PyPlot.
- Enable deselect/hide of plotted series via Object Manager.
- Add option to plot only smoothed derivative plots (skip raw derivatives) for VSM Temperature Scan.
- Use comments rows in Origin workbooks, set titles/labels to match PyPlot, and ensure Y labels fit without cropping; disable speed mode and enable anti-aliasing.

## In Progress
- Add Data Builder sections for VSM hysteresis loops, VSM temperature scan, and DMA iso-stress (reuse PyPlot plotting scripts; handle multiple graphs per microwire).
- Add a Compare section to filter/select microwires and compare their data/graphs side-by-side.
- Add DMA iso-stress PyPlot plugin (settings panel + plot tabs from TA DMA TXT files).
- Stabilizing VSM Temperature Scan exports (Origin/TXT) and plot embeddings.
- Stress/Temp plugins: fix Origin export parity (titles on top axis, manual sample labels, delta labels, units/comments in workbooks, no invalid LT errors); stop PyPlot stress sens cropping.
- Monitor Microwire Data Builder project-load responsiveness after the new progress/pumping change.
- Verify Data Builder launch responsiveness with background pending scans and confirm fullscreen shows bottom controls.
- Fix Data Builder fullscreen/maximized geometry handling.
- Ensure microscope manual entry advances to the next cell on Enter.
- Fix current density phase picking so Af1/Af2/etc. columns are respected.
- Assemble preview: column picker, column order, multi-column sorting, graph preview panel, and HTML export (awaiting confirmation).
- Assemble: allow manual microscope table data to satisfy microscope inputs without OCR payloads.
- Strain section: auto-fill d from microscope, editable weight/stress sync, and export stress + mode details.
- Strain entry form: keep Add entry after saving a new row and let Enter submit the form.
- Microscope refresh: preserve reviewed d/D values, keep review colours in sync, and make Tab advance between d/D cells.
- Strain selector dropdown: use taller popup when space allows and auto-focus it after saving a row.
- Remove remaining Qt warnings on Data Builder refresh (geometry, QBasicTimer, commitData).
- Verify Microwire microscope overrides: auto-review on override, d-field focus, comma normalization, arrow navigation, and preview sizing.
- Check Microwire microscope UI colours (reviewed green/red, error log highlighting) and preview/table splitter spacing on varied screens.
- Confirm Microwire microscope stacked previews, column auto-fit, and bottom control visibility after resizing/fullscreen.
- Validate microscope inline editing/review workflow: Enter navigation (`d`→`D`→next row), project-persisted per-cell review flags, row highlighting for missing images, core/glass preview swapping without zoom, and no `QWindowsWindow::setGeometry` warnings when maximizing.

## Done
- Added cursor readout scaffolding and duplicate-temperature averaging for VSM Temp Scan; integrated tab-selection fixes to avoid MDI proxy crashes.
