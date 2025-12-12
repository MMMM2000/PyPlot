# TODO

## Planned
- Fix initial Project Explorer/Object Manager layout so panes render correctly without reopening; ensure status-bar cursor readout is fully visible.
- Make the shared Zoom/Pan/Rescale toolbar functional across all plugins/canvases.
- Persist “Dark graphs” preference between sessions; monitor the new per-plugin import/export folder memory for any regressions.
- VSM Temperature Scan: finalize legends (arrows only; red/blue, orange/green), allow smoothing on derivatives, persist plot options, support per-series hide/show from Object Manager, and embed plots at usable sizes.
- VSM Temperature Scan Origin export: one workbook per sample with Data/Smoothed/Derivative sheets, comments matching legends, consistent colors/anti-aliasing (no speed mode), unclipped axis labels, better graph names/titles, and symbol size set to 1.
- Current Annealing: enlarge embedded plots; ensure legend symbols render with text color following line color.
- Add bottom/status cursor readout shared across PyPlot.
- Enable deselect/hide of plotted series via Object Manager.
- Add option to plot only smoothed derivative plots (skip raw derivatives) for VSM Temperature Scan.
- Use comments rows in Origin workbooks, set titles/labels to match PyPlot, and ensure Y labels fit without cropping; disable speed mode and enable anti-aliasing.

## In Progress
- Stabilizing VSM Temperature Scan exports (Origin/TXT) and plot embeddings.
- Stress/Temp plugins: fix Origin export parity (titles on top axis, manual sample labels, delta labels, units/comments in workbooks, no invalid LT errors); stop PyPlot stress sens cropping.
- Monitor Microwire Data Builder project-load responsiveness after the new progress/pumping change.
- Verify Data Builder launch responsiveness with background pending scans and confirm fullscreen shows bottom controls.
- Verify Microwire microscope overrides: auto-review on override, d-field focus, comma normalization, arrow navigation, and preview sizing.
- Check Microwire microscope UI colours (reviewed green/red, error log highlighting) and preview/table splitter spacing on varied screens.
- Confirm Microwire microscope stacked previews, column auto-fit, and bottom control visibility after resizing/fullscreen.
- Validate microscope inline editing/review workflow: Enter navigation (`d`→`D`→next row), project-persisted per-cell review flags, row highlighting for missing images, core/glass preview swapping without zoom, and no `QWindowsWindow::setGeometry` warnings when maximizing.

## Done
- Added cursor readout scaffolding and duplicate-temperature averaging for VSM Temp Scan; integrated tab-selection fixes to avoid MDI proxy crashes.
