# Simple Tk Scripts Plan

## Goals
- Provide a standalone Tkinter UI suite that replaces PyPlot for quick workflows instead of acting as a fallback to it.
- Cover every plotting plugin with a self-contained single-file Tkinter app that supports: selecting files/folders, plotting via Matplotlib *and* Origin, and exporting “nicely formatted” TXT data (one TXT per plotted graph containing long names, units, comments, etc.).

## Script Architecture
Shared helpers inside `experiments/simple_scripts/_shared.py` keep Tk boilerplate and threading logic in one place, but each plugin still ships a standalone, runnable script that only depends on `_shared.py`, standard libraries, and the plugin's own core module.
1. **Per-plugin script (single file):**
   - `App(tk.Tk)` subclass with dark-mode friendly ttk theme (use `ttkthemes` if available, else custom style).
   - Top toolbar: “Import files…”, “Import folder…”, optional plugin-specific toggles (baseline, smoothing,…).
   - Middle pane: `Listbox` showing queued inputs, optional log text.
   - Bottom row: `Plot Matplotlib`, `Plot Origin`, `Export TXT`, progress indicator, “Quit” button.
   - Worker thread (or `threading.Thread` with queue) so Origin/matplotlib work doesn’t freeze UI.
2. **Plugin integration:**
   - Reuse existing plugin core modules whenever possible (e.g., `plotting/plugins/temperature_sensitivity/core.py` already exposes `load_data`, `plot_variable`, `prepare_temperature_table`). Only wrap the necessary subset to drive Matplotlib/Origin/TXT.
   - For very custom workflows (e.g., `hsw_distribution` dialogs), begin with read-only (Matplotlib only) script, then iterate.
3. **Dark mode:**
   - Use custom `ttk.Style` to set dark palette.
   - Provide toggle to switch to light theme per session.

## Implementation Order
1. **Temperature Sensitivity** – already has rich core helpers; acts as template.
2. **Stress Sensitivity / Stress Dependence** – similar to temp; validate shared export helper.
3. **Current Annealing** – exercise live data requirements (maybe only accept CSV outputs).
4. **VSM Temperature Scan** – new workflow that mirrors the VSM hysteresis parser but plots signal X vs temperature on dual Y axes (10 kOe vs 50 Oe).
5. **PDF Plotter / Misc** – handle multi-file ZIP/TXT exports last.

## Deliverables per Script
- `experiments/simple_scripts/<plugin_slug>.py` with docstring describing inputs/outputs.
- A README entry (docs/simple_scripts_plan.md references) that links each script to original plugin.
- Optional launcher hook (from `experiments/__init__.py`) if quick access via PyPlot launcher is desired.

## Outstanding Questions
- How much of each plugin’s configurability should be exposed? (Proposal: start with most-used toggles only; add advanced dialogs later.)
- Should TXT export match PyPlot workbook schema or be simplified (e.g., CSV with column metadata at top)?
- Origin requirement: confirm the user’s environment includes `originpro`; scripts should gracefully warn if not.
- Would a tiny shared helper module ever be worth the maintenance cost? If yes, it must live inside `experiments/simple_scripts` (or a sibling) so the toolkit never depends on the main PyPlot codebase.

## Next Steps
1. Create `_shared.py` scaffold with file pickers, theme handling, background worker helper, and placeholder export methods.
2. Implement the Temperature Sensitivity script on top of `_shared.py` and validate End-to-End: import sample files → Matplotlib plot → Origin plot → TXT export.
3. Gather feedback, then replicate pattern across remaining plugins.

## Launcher Integration & Testing
- Add a toggle under the Launcher’s Developer menu that controls whether each Plotting entry opens the legacy PyQt plug-in or launches the corresponding Tk script. Store the preference (e.g., in QSettings or a JSON file) so the selection persists.
- Provide convenience helpers (`launcher.py`) to discover simple scripts dynamically from `experiments/simple_scripts`.
- Basic tests can live under `tests/simple_scripts/test_<plugin>.py`, focusing on the parsing/export helpers (GUI interactions stay manual). Start by unit-testing the file parsers (e.g., VSM temperature scan) and TXT export formatters; Origin/Matplotlib entry points can remain smoke-tested manually since they hit external apps.
