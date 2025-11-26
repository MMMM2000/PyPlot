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

## Done
- Added cursor readout scaffolding and duplicate-temperature averaging for VSM Temp Scan; integrated tab-selection fixes to avoid MDI proxy crashes.
