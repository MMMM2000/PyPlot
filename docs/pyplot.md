# PyPlot Overview

PyPlot provides the common desktop workbench: file import, worksheet management, graph tooling, Origin export, and project save/restore all live here so plug-ins only need to supply their domain-specific panels and load/generate logic. This page tracks those shared capabilities and points to the plug-ins that build on them.

## Workbench Basics

- **Project Explorer** lists imported workbooks, worksheets, and generated graphs. Every plug-in uses the same tree so you can keep one set of worksheets available while switching tools.
  Empty-state behavior: `Imported Data` now appears only after at least one workbook/sheet is actually imported.
  Readability note: long names/paths now use middle elision, improved column sizing, alternating rows, and tooltips with full text so dense imports remain navigable.
- **Object Manager** mirrors the current Matplotlib tab (axes, lines, legends, annotations) and lets you tweak selections via the toolbar actions.
- **Legend sync** now follows line visibility toggles and graph-format applies: legends are rebuilt from currently visible lines so hidden series are not listed.
- **Toolbars** (Plugin, Plot actions, Navigation, Format) live at the top. Enabled actions now show a subtle highlight so you can immediately see what is clickable; disabled items stay muted.
- **Graph formatting** is shared across plugins: PyPlot exposes a single shared `Graph formatting` section (de-duplicated across plugin menus) for title/X/Y labels (with per-label show/hide checkboxes), font sizes, tick size/width, tick mode (`Auto`, by increment, or by target count), figure width/height, axes aspect mode (`Auto`/`Equal`/custom ratio), line+marker size, linear/log axis scale, explicit axis limits, grid, and legend controls, then apply to the current graph or all open graphs.
  Graph-format layout: the Graph formatting window is now tabbed (`Text`, `Axes`, `Ticks`, `Legend`) so legend location/font/columns/symbol/follow-colour/draggable options are managed in the same shared place instead of per-plugin dialogs.
  Axis value formulas: each axis now supports a display factor expression (for example `10^-3`) with optional unit-label reflection.
  Access note: use the top-toolbar `Graph formatting` section button to open the dedicated movable Graph formatting dialog (not a clipped popover).
- **Axis unit style** defaults to square brackets in shared graphs (for example `Temperature [°C]`, `Strain [%]`).
- **Large-font safety**: graph-format applies run a layout fit pass so oversized title/label/tick fonts are kept inside the canvas bounds.
- **Direct on-canvas editing** is available on Matplotlib graphs: double-click title/X/Y label text, legend entries/box, or near an axis line to open the shared movable Graph formatting dialog, focused on the relevant controls (labels, legend, or axis scale/limits). If shared controls are unavailable for a host/plugin, PyPlot falls back to the legacy direct-edit dialogs.
  Object Manager parity: double-clicking a legend item in Object Manager now opens the same shared Graph formatting legend controls before falling back to legacy dialogs.
- **Dock switchers** sit on the left/right edges so you can collapse/restore the Project Explorer, Message Log, and Object Manager without losing their placement (enabled across platforms). Switchers now use click-to-toggle behavior (no hover pop-out/auto-collapse) to avoid resize flicker.
- **Settings → Graph options** provides shared defaults for all plugins plus optional per-plugin overrides (for example grid/legend defaults, font sizes, line width/marker size, default figure width/height, legend defaults) so plugin-specific behavior can diverge without duplicating UI code. The dialog now uses `Apply`, `Cancel`, and `Reset to defaults`; `Apply` immediately refreshes all open graphs.
- **Responsive side docks**: Project Explorer/Object Manager widths now reflow on window resize and expand on large/maximized windows, so the side panels do not stay locked to tiny startup widths.
- **Undo/Redo** are exposed on the Edit menu (Ctrl+Z / Ctrl+Y). They track tab changes, worksheet tabs, and other session actions.
- **Saving** uses `.pypj` project files. When a session contains imported data or generated worksheets PyPlot prompts you to save, discard, or cancel if you try to close the window.
- **Folder memory** is scoped per plug-in. Import file pickers, graph saves, and TXT exports reopen in the last folder used by that plug-in instead of sharing a single global path; plug-ins can also call `PyPlotPlugin.preferred_export_directory(...)` / `remember_export_directory(...)` to share the same history.
- **Project filenames** default to `<plugin name> YYYY-MM-DD.pypj` (for example, `VSM Temperature Scan 2025-12-03.pypj`). If no plug-in is selected, the suggested name is `pyplot YYYY-MM-DD.pypj`.
- **Legends** default to “text colour follows plot” for every plug-in. Legend options (show symbols, placement, orientation, drag, follow colours) are remembered per plug-in between sessions so each workflow keeps its own defaults.
- **Fullscreen**: maximizing any graph/workbook hides the others and maximizes the active subwindow; switching tabs or double-clicking a different graph keeps fullscreen on (only the active window is visible) until you restore a window to normal.
- **Graph window geometry**: shared MDI graph windows preserve the figure aspect ratio during window resize and maximize/restore transitions, so displayed proportions match saved graph proportions.
  Cascade sizing: with one visible graph in cascade mode, PyPlot now keeps cascade-style window sizing instead of an oversized first window.
- **Cascade layout persistence**: in cascade mode, activating another graph no longer re-cascades/repositions windows automatically, so manual graph window positions are preserved while you work.
- **Window arrangement**: the shared `Window` menu exposes `Cascade`, `Tile Vertical`, and `Tile Horizontal` for every plugin. Default graph/workbook view is `Cascade`.
- **Legend auto layout**: when legend orientation is `Auto`, PyPlot now prefers vertical layout for dense/long series labels and uses horizontal rows only when labels/space allow it.
- **TXT exports**: the default filename mirrors the current workbook/plot label so exported TXT/CSV files match the names shown in the Project Explorer and carry the sample/procedure context baked into those labels. Shared TXT export now also falls back to visible Matplotlib lines when a plugin does not populate explicit line-state metadata.
- **Shared plot workbooks**: for plug-ins that do not implement their own workbook builder, PyPlot now auto-creates a `Plot data` workbook per graph tab (XY column pairs from plotted lines). This keeps `Export workbooks to Origin...` and worksheet tooling available consistently across plotting plug-ins.
- **Shared Origin fallback**: when a plug-in uses the base `Open in Origin...` action, PyPlot exports the active plug-in’s shared plot workbooks to Origin and creates Origin graphs from those worksheets, so the button works even without a plug-in-specific Origin export implementation. Shared Origin workbook export keeps the Origin session open after transfer.
- **Origin worksheet metadata convention (shared export)**: shared exports apply `Long Name = physical quantity`, `Units = unit`, and `Comments = series/legend label` consistently for all columns. For shared plot workbooks, each X/Y pair now inherits axis metadata from the actual source axis (including multi-axis figures) instead of only descriptor-level labels.
- **Graph export**: Save graph supports `PNG`, `PDF`, and `SVG`.
  Save dialog memory: the last selected graph-export format is remembered and reused as the default next time.
- **Check outliers** now performs a worksheet scan (IQR-based with z-score fallback for low-spread columns), shows a per-sheet summary, and can remove flagged rows in-place across affected worksheets.

## Origin export checklist
- Mirror the Matplotlib view: same title (top X label), axis labels, sample ordering, and delta annotations; hide Origin tick labels and draw manual sample labels when needed.
- Set graph/axis titles with Origin-compatible label commands (`label -n ...` / `label -xb/-yl/-xt/-yr`) so titles render reliably across Origin builds where `PAGE.ANTIALIAS` and some direct axis/title properties are not available.
- In shared exports, prefer built-in Origin graph templates (`line`, `scatter`) before the user default template to avoid user-template LabTalk side effects (for example recurring `LEGEND.SMARTPOS` errors).
- Preserve sample labels on X and long name/units/comments rows in the Origin worksheets (baseline, deltas, relative values documented).
- Match line/symbol styles, widths, sizes, and legend entries; ensure text follows line/marker colour in both light/dark graph modes.
- For shared multi-axis exports (for example dual-axis overlays), group XY pairs by axis-title metadata and plot each group on its own linked Origin layer so displacement/load and strain/stress do not collapse onto one Y scale.
- For shared dual-axis overlays with identical segment labels across groups (for example `Loading 1` in both load and stress groups), hide duplicate secondary-layer traces after axis scaling so the exported graph shows one visible curve set by default.
- Use descriptive graph names/long names that match the Matplotlib title and include distinguishing metadata (field strength, temperature, or variant) when multiple graphs share a sample name.
- Build worksheets with units and comments filled (including baselines/deltas/relative columns) and avoid terminal spam (disable tqdm/console progress); keep graph extents so nothing is cropped after export.

## Built-in Plug-ins

| Plug-in name            | Module                                                | Notes |
|-------------------------|-------------------------------------------------------|-------|
| Temperature Sensitivity | `plotting.plugins.temperature_sensitivity`            | Imports the TSV/CSV/Origin-like files used for T1/T2 analysis. Auto-loads data after import and creates worksheets annotated with units. |
| Temperature Dependence  | `plotting.plugins.temperature_dependence`             | Generates per-variable Matplotlib plots from the temperature dependence CSV set. |
| Stress Sensitivity      | `plotting.plugins.stress_sensitivity`                 | Combines stress sweeps and overlays key metrics. |
| Stress Dependence       | `plotting.plugins.stress_dependence`                  | Converts TXT exports into worksheets + line graphs. |
| Shape Memory Stress/Strain | `plotting.plugins.shape_memory_stress_strain`     | Loads Manual Stress/Strain Logger TXT files and plots segmented loops as `Loading 1`, `Unloading 1`, `Loading 2`, ... with configurable layout: separate load/stress tabs or a single dual-axis overlay (load/displacement left+bottom, stress/strain right+top). Leading zero-load points are trimmed until the first non-zero load to remove pre-load baseline rows. The selected layout mode is remembered between sessions, and dual-axis overlays keep a single shared segment legend (`Loading 1`, etc.) instead of duplicated `Load/Stress` legend groups. |
| Current Annealing       | `plotting.plugins.current_annealing`                  | Splits batches by annealing direction and exposes workbook exports. Defaults now align better with shared PyPlot graph sizing/label style so formatting behavior is consistent across plugins. |
| VSM Hysteresis Loops    | `plotting.plugins.vsm_hysteresis`                     | Wraps the legacy VSM plotter with the shared tooling, including Origin exports. Workbooks group each temperature graph into a single worksheet with XY column pairs per angle. |
| VSM Temperature Scan    | `plotting.plugins.vsm_temperature_scan`               | Plots Signal X vs Temperature with heating/cooling splits; can combine low/high field runs into a dual-axis plot; Origin/TXT exports carry per-section legends and TXT filenames embed sample, temperature span, and field strength. Core parsing/export logic now lives in `plotting.plugins.vsm_temperature_scan.core` so PyPlot and Data Builder share one implementation. |
| VSM Isotherms           | `plotting.plugins.vsm_isotherms`                      | Parses VIR exports (`.VSM-VIR-Data`) and groups plots by field angle (for example separate 0° and 90° graphs with all same-angle temperatures overlaid); duplicate same-temperature runs are consolidated per angle (full-field curves preferred) and workbook sheets are auto-generated for both isotherms and entropy tables. Entropy field levels (`ΔH`) can be user-defined in the plugin settings, or left blank for automatic levels. |
| DMA Iso-Stress          | `plotting.plugins.dma_iso_stress`                     | Parses TA DMA IsoStress TXT files into temperature/strain plots per stress level; includes a Graph formatting panel (title/X/Y label text + visibility toggles, grid, legend location, editable legend-entry text, line width/font size, per-axis tick mode `Auto`/`By increment`/`By count`, optional axis limits, apply current/all, and selective copy to chosen DMA graphs), plus Origin export from the toolbar. PyPlot project save/load now restores DMA plotted tabs and their formatting state (not only imported worksheets). Parsing is plugin-local (`plotting.plugins.dma_iso_stress.parser`) and Tk-independent. |
| FMR                     | `plotting.plugins.fmr`                                | Plots Field vs X/Y voltage traces from FMR CSV files and exports to Origin with X/Y columns; includes an option to combine all samples into a single X-only plot with a per-sample legend, lock-in phase rotation controls (auto flatten-Y or manual angle), and optional automatic forward/back sweep field alignment. |
| Maxion / PDF / HSW tools| `plotting.plugins.maxion_continuous`, `...pdf_plotter`| These are embedded legacy UIs launched inside the PyPlot frame. |

Use `plotting/plugins/__init__.py` as the registry when you add a new tool. Provide `requires_imported_data = True` if the plug-in needs imported worksheets before plotting, and give its Plot button a descriptive label such as “Plot Temperature Sensitivity” so users always know what the action will generate.
Plugin authoring note: prefer shared PyPlot features (`save graph`, `graph formatting`, `TXT export`, shared `Open in Origin`, and shared plot-workbooks) and only override per-plugin behavior when the workflow truly needs custom handling. Use `PyPlotPlugin.apply_shared_action_state(...)`, `PyPlotPlugin.clear_plot_tabs(...)`, and `PyPlotPlugin.run_origin_export(...)` to avoid repeating boilerplate per plugin. If a plugin already manages its own workbook lifecycle, set `uses_shared_plot_workbooks = False`.

## Importing Data

1. Use the **Import data…** button (or the Data menu) to select files/folders (multi-select folders are supported).
2. Supported formats: CSV/TSV/TXT/XLS/XLSX/XLSM/JSON/VSM `.vsm-hys-data`, `.vsm-tscn-data`, `.vsm-vir-data`. Plug-ins can add their own loaders (see `PyPlotPlugin.load_data` implementations).
3. After import, plug-ins that set `auto_load_on_import` can register their own workbooks automatically. Plug-ins that declare `requires_imported_data` keep Plot disabled until Load Data (or auto-load) populates their data.
4. All worksheets live under `Imported Data` → `<folder>` → `<workbook>` so every plug-in can reuse them (export to Origin, duplicate, edit columns, etc.).

## Logs

- The **Message Log** dock records plug-in output, path skips, and Origin export diagnostics. Toggle it via the dock switcher on the left edge whenever you need to inspect warnings.
- Project Explorer/Object Manager activation errors (for example, stale item references) are now captured in the Message Log instead of hard-aborting the app.

## Extending / Debugging

- Shared UI helpers live in `plotting/pyplot/window.py` so plug-ins can reuse the same worksheet/graph machinery without reimplementing it.
- Use `docs/todo/pyplot_migration_todo.md` for open work. Update this `pyplot.md` file when you add major features or new plug-ins so other developers can discover them quickly.
- Window layout conventions:
  - Graph/worksheet windows are MDI subwindows (no visible tab bar). Default arrangement is cascading windows; each subwindow keeps the graph aspect ratio during manual resize and arrangement changes.
  - When only one subwindow is visible in windowed mode, it expands to fill the available MDI viewport so content is not cropped at the bottom after fullscreen/resize transitions.
  - macOS maximize geometry now compensates subwindow frame/title-bar extents so the active graph canvas fills the viewport without a clipped bottom strip.
  - Maximizing any subwindow maximizes all of them when you switch via the Project Explorer; restoring one returns all to windowed mode.
  - When adding or updating plug-ins, keep these sizing/fullscreen rules intact and refresh this document if the behavior changes.
- Subwindow lifecycle:
  - The close button hides a graph instead of destroying it; reopen via Project Explorer → Plots. Keep windows in fullscreen/windowed mode until the user changes it, and sync that state across subwindows when switching.
  - Visibility queue cleanup now removes deleted subwindow references before activation/limit checks, preventing stale `wrapped C/C++ object ... has been deleted` exceptions.

Feel free to expand these sections with screenshots, plugin-specific quirks, or Origin export caveats as the toolset grows.

## Ongoing problems (investigating)
- Initial Project Explorer/Object Manager layout sometimes appears squashed until the docks are toggled; window content can shift down causing the status-bar X/Y readout to clip. Keep retrying layout fixes until this is resolved.
