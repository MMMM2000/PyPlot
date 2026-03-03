# TODO

## Planned
- VSM Temperature Scan Origin export: one workbook per sample with Data/Smoothed/Derivative sheets, comments matching legends, consistent colors/anti-aliasing (no speed mode), unclipped axis labels, better graph names/titles (include field labels), symbol sizes/line widths matching PyPlot, and plot all selected datasets.
- Use comments rows in Origin workbooks, set titles/labels to match PyPlot, and ensure Y labels fit without cropping; disable speed mode and enable anti-aliasing.
- Origin exports: verify line/symbol styles and graph titles mirror PyPlot across plugins (VSM hysteresis, VSM temp scan, DMA iso-stress, FMR).

## In Progress


## Done
- Current Annealing Logger: add configurable start current (default 10 mA) for supplies with higher minimum output.
- Current Annealing Logger: add configurable voltage limit (default 30 V) for higher-voltage supplies.
- Added cursor readout scaffolding and duplicate-temperature averaging for VSM Temp Scan; integrated tab-selection fixes to avoid MDI proxy crashes.
- Verified/covered multi-folder import dialog flow (including repeated native folder selection) for Windows-style Import Folders behavior.
- Verified dark-graph preference persistence and plugin-scoped import/export folder memory behavior with regression coverage.
- VSM Temperature Scan: added smoothed-derivative-only plotting/export path (raw derivatives can remain disabled).
- VSM Temperature Scan: project save/load now persists plot options (split/combine, derivative toggles, smoothing windows, overlay state) with regression coverage.
- VSM Temperature Scan: dual-field combine behavior (including mixed-field axis/legend handling and Origin-side export expectations) is covered by regression tests.
- VSM Temperature Scan: shared outlier workflow is verified on plugin workbooks (detect + preview + confirm delete path through PyPlot outlier tooling).
- Shared Zoom/Pan/Rescale toolbar now keeps navigation mode active across graph-tab switches and plugin canvases.
- Verified initial Project Explorer/Object Manager layout fix behavior across show/resize/maximize flows; primary docks are refreshed on window-state changes and status-bar cursor readout stays visible under narrow layouts with active progress widgets.
- VSM Temperature Scan legends are finalized for non-Origin flows: section order is preserved, direction labels stay arrow-based, and field-pair colors are stable (red/blue for first field, orange/green for second field).
- VSM Temperature Scan embedded previews now render at a larger default size in the builder gallery for readability.
- Current Annealing embedded plots were enlarged in builder previews, and Matplotlib legend text now follows line color as a final post-style pass.
- Current density section includes As1/Af1/Ms1/Mf1 and As2/Af2/Ms2/Mf2 plus repeat/delta fields (including Mf-Af deltas) in Assemble output paths.
- Transition temps (As/Af/Ms/Mf) are merged into Assemble output columns and reflected in column-group selection paths.
- Verified Object Manager line-item visibility toggles keep plotted-series hide/show and legend entries in sync, including graphs with explicit legends.
