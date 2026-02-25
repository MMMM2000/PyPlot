# TODO

## Planned
- Verify initial Project Explorer/Object Manager layout fix so panes render correctly without reopening and side docks scale appropriately after maximize; ensure status-bar cursor readout is fully visible.
- Verify multi-folder import dialog supports selecting multiple directories on Windows (Import Folders).
- VSM Temperature Scan: confirm combine low/high field runs (dual-axis) option, axis labels, and Origin export labels for mixed-field samples.
- VSM Temperature Scan: design and implement outlier detection workflow (criteria, preview, confirm delete).
- Make the shared Zoom/Pan/Rescale toolbar functional across all plugins/canvases.
- Persist “Dark graphs” preference between sessions; monitor the new per-plugin import/export folder memory for any regressions.
- Current density section: add As1/Af1/Ms1/Mf1 and As2/Af2/Ms2/Mf2 fields with delta columns for repeat measurements (including Mf-Af deltas).
- Transition temps: confirm whether As/Af/Ms/Mf should be merged into Assemble/HTML exports (and column picker groups).
- VSM Temperature Scan: finalize legends (arrows only; red/blue, orange/green; preserve section order), allow smoothing on derivatives, persist plot options, support per-series hide/show from Object Manager, and embed plots at usable sizes.
- VSM Temperature Scan Origin export: one workbook per sample with Data/Smoothed/Derivative sheets, comments matching legends, consistent colors/anti-aliasing (no speed mode), unclipped axis labels, better graph names/titles (include field labels), symbol sizes/line widths matching PyPlot, and plot all selected datasets.
- Current Annealing: enlarge embedded plots; ensure legend symbols render with text color following line color.
- Add bottom/status cursor readout shared across PyPlot.
- Enable deselect/hide of plotted series via Object Manager (ensure line items appear even when legends are present).
- Add option to plot only smoothed derivative plots (skip raw derivatives) for VSM Temperature Scan.
- Use comments rows in Origin workbooks, set titles/labels to match PyPlot, and ensure Y labels fit without cropping; disable speed mode and enable anti-aliasing.
- Origin exports: verify line/symbol styles and graph titles mirror PyPlot across plugins (VSM hysteresis, VSM temp scan, DMA iso-stress, FMR).

## In Progress


## Done
- Current Annealing Logger: add configurable start current (default 10 mA) for supplies with higher minimum output.
- Current Annealing Logger: add configurable voltage limit (default 30 V) for higher-voltage supplies.
- Added cursor readout scaffolding and duplicate-temperature averaging for VSM Temp Scan; integrated tab-selection fixes to avoid MDI proxy crashes.
