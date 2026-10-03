# PyPlot Qt UI laboratory

The UI laboratory is a standalone PyQt6 prototype for evaluating launcher and
plotting-workspace changes before they are integrated into production PyPlot.
It uses synthetic data only and does not read projects, application settings,
hardware, or persistent user storage.

Run it from the repository root:

```powershell
uv run python scripts\pyplot_ui_lab.py
```

## Launcher design handoff

The launcher exploration is parked for future design iteration. No variant has
been approved for production. The current production launcher remains the user's
preferred baseline; these experiments should not be treated as a chosen upgrade.

Three runnable variants borrow the visual direction from TMA workspace PR #303:

- `a`: detailed single-column rows, 760 x 540 logical pixels.
- `b`: compact two-column browser, 720 x 460 logical pixels.
- `c`: compact single-column list with quiet tabs and an action dock, 700 x 500
  logical pixels.

```powershell
uv run python scripts\pyplot_ui_lab.py --launcher-variant b
uv run python scripts\pyplot_ui_lab.py --render-launchers
```

Screenshots are regenerated under ignored
`artifacts/pyplot-ui-lab/pr303-launchers/`. Use the native Windows Qt platform for
visual captures: the offscreen platform on this machine can lack system fonts.
The window sizes above are logical pixels; Windows scaling changes the output
image dimensions. The lab uses synthetic tool records and opens a synthetic plot
workspace rather than launching production tools.

Future work should begin by comparing the current launcher with these native Qt
renders and improving typography, density, selection, and navigation before
integrating a design. Keep movable half-width graphs with an explicit maximize
option. The broader plotting functionality review still needs production fixes;
this laboratory alone does not resolve those findings.

## Prototype scope

- Searchable, category-based launcher with explicit readiness states.
- Standard Qt menus, toolbars, icons, lists, splitters, and keyboard focus.
- `QMainWindow` plotting workspace with `QDockWidget` side panels.
- Real `QMdiArea` graph windows that default to roughly half the available
  workspace and can be moved, tiled, cascaded, maximized, and restored.
- Real Matplotlib canvas rendering with synthetic current-annealing data.
- Timer-driven task feedback that leaves the workspace interactive.
- Accessible names on the primary controls.

The prototype is intentionally not registered in the production launcher and
does not reuse production project or plugin state.

## Findings from Windows captures

The prototype is captured at effective 100%, 125%, and 150% Windows scaling
under `artifacts/pyplot-ui-lab/captures/`.

- Native Qt controls and MDI chrome are a more reliable visual target than the
  Figma exploration.
- Half-width placement must occur after the first shown layout pass; sizing a
  graph during construction produces a smaller window after docks settle.
- A fixed graph minimum width conflicts with half-width placement on constrained
  high-DPI workspaces.
- At logical window widths below 1100 pixels, starting with the properties dock
  hidden preserves a readable half-width graph. The dock remains available from
  the View menu.
- A non-modal status-bar task is clearer and less disruptive than the current
  modal progress wrapper, but production work still requires moving file and
  plotting preparation off the GUI thread.

## Production boundary

This laboratory is a design and behavior reference, not a replacement for
`launcher.MasterLauncher` or the production PyPlot workbench. Production
migration should begin with the shared shell and one representative plotting
plugin, then expand only after real-data behavior, DPI, accessibility, and
background execution have been verified.
