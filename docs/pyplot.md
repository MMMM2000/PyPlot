# PyPlot Overview

PyPlot provides the common desktop workbench: file import, worksheet management, graph tooling, Origin export, and project save/restore all live here so plug-ins only need to supply their domain-specific panels and load/generate logic. This page tracks those shared capabilities and points to the plug-ins that build on them.

## Workbench Basics

- **Project Explorer** lists imported workbooks, worksheets, and generated graphs. Every plug-in uses the same tree so you can keep one set of worksheets available while switching tools.
  Empty-state behavior: `Imported Data` now appears only after at least one workbook/sheet is actually imported.
  Readability note: long names/paths now use middle elision, improved column sizing, alternating rows, and tooltips with full text so dense imports remain navigable.
- **Object Manager** mirrors the current Matplotlib tab (axes, lines, legends, annotations) and lets you tweak selections via the toolbar actions.
- **Legend sync** now follows line visibility toggles and graph-format applies: legends are rebuilt from currently visible lines so hidden series are not listed.
- **Toolbars** (Plugin, Plot actions, Navigation, Format) live at the top. Enabled actions now show a subtle highlight so you can immediately see what is clickable; disabled items stay muted.
- **Graph formatting** is shared across plugins: PyPlot exposes a single shared `Graph formatting` section (de-duplicated across plugin menus) for title/X/Y labels (with per-label show/hide checkboxes), font sizes, tick size/width, tick mode (`Auto`, by increment, or by target count), figure width/height, axes aspect mode (`Auto`/`Equal`/custom ratio), line+marker size, linear/log axis scale, explicit axis limits, grid, and legend visibility, then apply to the current graph or all open graphs.
  Axis value formulas: each axis now supports a display factor expression (for example `10^-3`) with optional unit-label reflection.
  Access note: use the top-toolbar `Graph formatting` section button to open the dedicated movable Graph formatting dialog (not a clipped popover).
- **Axis unit style** defaults to square brackets in shared graphs (for example `Temperature [°C]`, `Strain [%]`).
- **Large-font safety**: graph-format applies run a layout fit pass so oversized title/label/tick fonts are kept inside the canvas bounds.
- **Direct on-canvas editing** is available on Matplotlib graphs: double-click title/X/Y label text or near an axis line to open the shared movable Graph formatting dialog, focused on the relevant controls (labels or axis scale/limits). If shared controls are unavailable for a host/plugin, PyPlot falls back to the legacy direct-edit dialogs.
- **Dock switchers** sit on the left/right edges so you can collapse/restore the Project Explorer, Message Log, and Object Manager without losing their placement.
- **Responsive side docks**: Project Explorer/Object Manager widths now reflow on window resize and expand on large/maximized windows, so the side panels do not stay locked to tiny startup widths.
- **Undo/Redo** are exposed on the Edit menu (Ctrl+Z / Ctrl+Y). They track tab changes, worksheet tabs, and other session actions.
- **Saving** uses `.pypj` project files. When a session contains imported data or generated worksheets PyPlot prompts you to save, discard, or cancel if you try to close the window.
- **Folder memory** is scoped per plug-in. Import file pickers, graph saves, and TXT exports reopen in the last folder used by that plug-in instead of sharing a single global path; plug-ins can also call `PyPlotPlugin.preferred_export_directory(...)` / `remember_export_directory(...)` to share the same history.
- **Project filenames** default to `<plugin name> YYYY-MM-DD.pypj` (for example, `VSM Temperature Scan 2025-12-03.pypj`). If no plug-in is selected, the suggested name is `pyplot YYYY-MM-DD.pypj`.
- **Legends** default to “text colour follows plot” for every plug-in. Legend options (show symbols, placement, orientation, drag, follow colours) are remembered per plug-in between sessions so each workflow keeps its own defaults.
- **Fullscreen**: maximizing any graph/workbook hides the others and maximizes the active subwindow; switching tabs or double-clicking a different graph keeps fullscreen on (only the active window is visible) until you restore a window to normal.
- **TXT exports**: the default filename mirrors the current workbook/plot label so exported TXT/CSV files match the names shown in the Project Explorer and carry the sample/procedure context baked into those labels.
- **Graph export**: Save graph supports `PNG`, `PDF`, and `SVG`.
  Save dialog memory: the last selected graph-export format is remembered and reused as the default next time.

## Origin export checklist
- Mirror the Matplotlib view: same title (top X label), axis labels, sample ordering, and delta annotations; hide Origin tick labels and draw manual sample labels when needed.
- Preserve sample labels on X and long name/units/comments rows in the Origin worksheets (baseline, deltas, relative values documented).
- Match line/symbol styles, widths, sizes, and legend entries; ensure text follows line/marker colour in both light/dark graph modes.
- Use descriptive graph names/long names that match the Matplotlib title and include distinguishing metadata (field strength, temperature, or variant) when multiple graphs share a sample name.
- Build worksheets with units and comments filled (including baselines/deltas/relative columns) and avoid terminal spam (disable tqdm/console progress); keep graph extents so nothing is cropped after export.

## Built-in Plug-ins

| Plug-in name            | Module                                                | Notes |
|-------------------------|-------------------------------------------------------|-------|
| Temperature Sensitivity | `plotting.plugins.temperature_sensitivity`            | Imports the TSV/CSV/Origin-like files used for T1/T2 analysis. Auto-loads data after import and creates worksheets annotated with units. |
| Temperature Dependence  | `plotting.plugins.temperature_dependence`             | Generates per-variable Matplotlib plots from the temperature dependence CSV set. |
| Stress Sensitivity      | `plotting.plugins.stress_sensitivity`                 | Combines stress sweeps and overlays key metrics. |
| Stress Dependence       | `plotting.plugins.stress_dependence`                  | Converts TXT exports into worksheets + line graphs. |
| Current Annealing       | `plotting.plugins.current_annealing`                  | Splits batches by annealing direction and exposes workbook exports. Defaults now align better with shared PyPlot graph sizing/label style so formatting behavior is consistent across plugins. |
| VSM Hysteresis Loops    | `plotting.plugins.vsm_hysteresis`                     | Wraps the legacy VSM plotter with the shared tooling, including Origin exports. Workbooks group each temperature graph into a single worksheet with XY column pairs per angle. |
| VSM Temperature Scan    | `plotting.plugins.vsm_temperature_scan`               | Plots Signal X vs Temperature with heating/cooling splits; can combine low/high field runs into a dual-axis plot; Origin/TXT exports carry per-section legends and TXT filenames embed sample, temperature span, and field strength. Core parsing/export logic now lives in `plotting.plugins.vsm_temperature_scan.core` so PyPlot and Data Builder share one implementation. |
| DMA Iso-Stress          | `plotting.plugins.dma_iso_stress`                     | Parses TA DMA IsoStress TXT files into temperature/strain plots per stress level; includes a Graph formatting panel (title/X/Y label text + visibility toggles, grid, legend location, editable legend-entry text, line width/font size, per-axis tick mode `Auto`/`By increment`/`By count`, optional axis limits, apply current/all, and selective copy to chosen DMA graphs), plus Origin export from the toolbar. PyPlot project save/load now restores DMA plotted tabs and their formatting state (not only imported worksheets). Parsing is plugin-local (`plotting.plugins.dma_iso_stress.parser`) and Tk-independent. |
| FMR                     | `plotting.plugins.fmr`                                | Plots Field vs X/Y voltage traces from FMR CSV files and exports to Origin with X/Y columns; includes an option to combine all samples into a single X-only plot with a per-sample legend, lock-in phase rotation controls (auto flatten-Y or manual angle), and optional automatic forward/back sweep field alignment. |
| Maxion / PDF / HSW tools| `plotting.plugins.maxion_continuous`, `...pdf_plotter`| These are embedded legacy UIs launched inside the PyPlot frame. |

Use `plotting/plugins/__init__.py` as the registry when you add a new tool. Provide `requires_imported_data = True` if the plug-in needs imported worksheets before plotting, and give its Plot button a descriptive label such as “Plot Temperature Sensitivity” so users always know what the action will generate.

## Importing Data

1. Use the **Import data…** button (or the Data menu) to select files/folders (multi-select folders are supported).
2. Supported formats: CSV/TSV/TXT/XLS/XLSX/XLSM/JSON/VSM `.vsm-hys-data`. Plug-ins can add their own loaders (see `PyPlotPlugin.load_data` implementations).
3. After import, plug-ins that set `auto_load_on_import` can register their own workbooks automatically. Plug-ins that declare `requires_imported_data` keep Plot disabled until Load Data (or auto-load) populates their data.
4. All worksheets live under `Imported Data` → `<folder>` → `<workbook>` so every plug-in can reuse them (export to Origin, duplicate, edit columns, etc.).

## Logs

- The **Message Log** dock records plug-in output, path skips, and Origin export diagnostics. Toggle it via the dock switcher on the left edge whenever you need to inspect warnings.
- Project Explorer/Object Manager activation errors (for example, stale item references) are now captured in the Message Log instead of hard-aborting the app.

## Extending / Debugging

- Shared UI helpers live in `plotting/pyplot/window.py` so plug-ins can reuse the same worksheet/graph machinery without reimplementing it.
- Use `docs/todo/pyplot_migration_todo.md` for open work. Update this `pyplot.md` file when you add major features or new plug-ins so other developers can discover them quickly.
- Window layout conventions:
  - Graph/worksheet windows are MDI subwindows (no visible tab bar). Default width is half of the viewport; height follows the subwindow’s aspect ratio and shrinks width if needed to fit vertically. Aspect ratio is locked during manual resizing.
  - When only one subwindow is visible in windowed mode, it expands to fill the available MDI viewport so content is not cropped at the bottom after fullscreen/resize transitions.
  - macOS maximize geometry now compensates subwindow frame/title-bar extents so the active graph canvas fills the viewport without a clipped bottom strip.
  - Maximizing any subwindow maximizes all of them when you switch via the Project Explorer; restoring one returns all to windowed mode.
  - When adding or updating plug-ins, keep these sizing/fullscreen rules intact and refresh this document if the behavior changes.
- Subwindow lifecycle:
  - The close button hides a graph instead of destroying it; reopen via Project Explorer → Plots. Keep windows in fullscreen/windowed mode until the user changes it, and sync that state across subwindows when switching.

Feel free to expand these sections with screenshots, plugin-specific quirks, or Origin export caveats as the toolset grows.

## Ongoing problems (investigating)
- Initial Project Explorer/Object Manager layout sometimes appears squashed until the docks are toggled; window content can shift down causing the status-bar X/Y readout to clip. Keep retrying layout fixes until this is resolved.
