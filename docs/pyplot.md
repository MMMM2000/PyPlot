# PyPlot Overview

PyPlot provides the common desktop workbench: file import, worksheet management, graph tooling, Origin export, and project save/restore all live here so plug-ins only need to supply their domain-specific panels and load/generate logic. This page tracks those shared capabilities and points to the plug-ins that build on them.

## Workbench Basics

- **Project Explorer** lists imported workbooks, worksheets, and generated graphs. Every plug-in uses the same tree so you can keep one set of worksheets available while switching tools.
- **Object Manager** mirrors the current Matplotlib tab (axes, lines, legends, annotations) and lets you tweak selections via the toolbar actions.
- **Toolbars** (Plugin, Plot actions, Navigation, Format) live at the top. Enabled actions now show a subtle highlight so you can immediately see what is clickable; disabled items stay muted.
- **Dock switchers** sit on the left/right edges so you can collapse/restore the Project Explorer, Message Log, and Object Manager without losing their placement.
- **Undo/Redo** are exposed on the Edit menu (Ctrl+Z / Ctrl+Y). They track tab changes, worksheet tabs, and other session actions.
- **Saving** uses `.pypj` project files. When a session contains imported data or generated worksheets PyPlot prompts you to save, discard, or cancel if you try to close the window.
- **Folder memory** is scoped per plug-in. Import file pickers, graph saves, and TXT exports reopen in the last folder used by that plug-in instead of sharing a single global path; plug-ins can also call `PyPlotPlugin.preferred_export_directory(...)` / `remember_export_directory(...)` to share the same history.
- **Legends** default to “text colour follows plot” for every plug-in. Legend options (show symbols, placement, orientation, drag, follow colours) are remembered per plug-in between sessions so each workflow keeps its own defaults.

## Origin export checklist
- Mirror the Matplotlib view: same title (top X label), axis labels, sample ordering, and delta annotations; hide Origin tick labels and draw manual sample labels when needed.
- Preserve sample labels on X and long name/units/comments rows in the Origin worksheets (baseline, deltas, relative values documented).
- Match symbol sizes/colours and legend entries; ensure text follows line/marker colour in both light/dark graph modes.
- Build worksheets with units and comments filled (including baselines/deltas/relative columns) and avoid terminal spam (disable tqdm/console progress); keep graph extents so nothing is cropped after export.

## Built-in Plug-ins

| Plug-in name            | Module                                                | Notes |
|-------------------------|-------------------------------------------------------|-------|
| Temperature Sensitivity | `plotting.plugins.temperature_sensitivity`            | Imports the TSV/CSV/Origin-like files used for T1/T2 analysis. Auto-loads data after import and creates worksheets annotated with units. |
| Temperature Dependence  | `plotting.plugins.temperature_dependence`             | Generates per-variable Matplotlib plots from the temperature dependence CSV set. |
| Stress Sensitivity      | `plotting.plugins.stress_sensitivity`                 | Combines stress sweeps and overlays key metrics. |
| Stress Dependence       | `plotting.plugins.stress_dependence`                  | Converts TXT exports into worksheets + line graphs. |
| Current Annealing       | `plotting.plugins.current_annealing`                  | Splits batches by annealing direction and exposes workbook exports. |
| VSM Hysteresis Loops    | `plotting.plugins.vsm_hysteresis`                     | Wraps the legacy VSM plotter with the shared tooling, including Origin exports. |
| VSM Temperature Scan    | `plotting.plugins.vsm_temperature_scan`               | Plots Signal X vs Temperature with heating/cooling splits; Origin/TXT exports carry per-section legends and TXT filenames embed sample, temperature span, and field strength. |
| Maxion / PDF / HSW tools| `plotting.plugins.maxion_continuous`, `...pdf_plotter`| These are embedded legacy UIs launched inside the PyPlot frame. |

Use `plotting/plugins/__init__.py` as the registry when you add a new tool. Provide `requires_imported_data = True` if the plug-in needs imported worksheets before plotting, and give its Plot button a descriptive label such as “Plot Temperature Sensitivity” so users always know what the action will generate.

## Importing Data

1. Use the **Import data…** button (or the Data menu) to select files/folders.
2. Supported formats: CSV/TSV/TXT/XLS/XLSX/XLSM/JSON/VSM `.vsm-hys-data`. Plug-ins can add their own loaders (see `PyPlotPlugin.load_data` implementations).
3. After import, plug-ins that set `auto_load_on_import` can register their own workbooks automatically. Otherwise clicking Plot is responsible for loading the selected files and rebuilding the per-graph workbooks.
4. All worksheets live under `Imported Data` → `<folder>` → `<workbook>` so every plug-in can reuse them (export to Origin, duplicate, edit columns, etc.).

## Logs

- The **Message Log** dock records plug-in output, path skips, and Origin export diagnostics. Toggle it via the dock switcher on the left edge whenever you need to inspect warnings.

## Extending / Debugging

- Shared UI helpers live in `plotting/pyplot/window.py` so plug-ins can reuse the same worksheet/graph machinery without reimplementing it.
- Use `docs/todo/pyplot_migration_todo.md` for open work. Update this `pyplot.md` file when you add major features or new plug-ins so other developers can discover them quickly.
- Window layout conventions:
  - Graph/worksheet windows are MDI subwindows (no visible tab bar). Default width is half of the viewport; height follows the subwindow’s aspect ratio and shrinks width if needed to fit vertically. Aspect ratio is locked during manual resizing.
  - Maximizing any subwindow maximizes all of them when you switch via the Project Explorer; restoring one returns all to windowed mode.
  - When adding or updating plug-ins, keep these sizing/fullscreen rules intact and refresh this document if the behavior changes.
- Subwindow lifecycle:
  - The close button hides a graph instead of destroying it; reopen via Project Explorer → Plots. Keep windows in fullscreen/windowed mode until the user changes it, and sync that state across subwindows when switching.

Feel free to expand these sections with screenshots, plugin-specific quirks, or Origin export caveats as the toolset grows.

## Ongoing problems (investigating)
- Initial Project Explorer/Object Manager layout sometimes appears squashed until the docks are toggled; window content can shift down causing the status-bar X/Y readout to clip. Keep retrying layout fixes until this is resolved.
