# PyPlot Migration Tracker

## 1. Inventory & Dependencies

### 1.1 Core PyPlot Runtime
- `plotting/pyplot/window.py` – main QMainWindow subclass, shared widgets, graph state helpers.
- `plotting/pyplot/app.py` – workbench orchestration, plugin registration, project persistence.
- `plotting/pyplot/console.py`, `plotting/python_console.py` – embedded Python console.
- Shared helpers: `plotting/shared/toolkit.py`, `plotting/shared/common.py`, `plotting/shared/backends.py`, `plotting/shared/config.py`, `plotting/shared/__init__.py`.

### 1.2 Active Plugins (wired through `PyPlotWorkbench`)
| Plugin name | Source module(s) | Legacy entry points | Notes |
|-------------|------------------|---------------------|-------|
| Temperature Dependence | `plotting/plugins/temperature_dependence/temp_dep_plugin.py` + `plotting/temperature_dependence/core.py` | _Removed_ | TXT export still routed through legacy helper; GUI re-exports the plugin class. |
| Temperature Sensitivity | `plotting/plugins/temperature_sensitivity/temp_sens_plugin.py` + `plotting/temperature_sensitivity/core.py` | _Removed_ | Legacy dialog now shims to the PyPlot plugin. |
| Current Annealing | `plotting/plugins/current_annealing/current_annealing_plugin.py` + `plotting/current_annealing/core.py` | _Removed_ | Uses `format_annealing_title`; compatibility wrapper exports the plugin. |
| Stress Dependence | `plotting/plugins/stress_dependence/stress_dep_plugin.py` + `plotting/stress_dependence/core.py` | _Removed_ | Legacy GUI still imports console helpers; now re-exports the plugin class. |
| Stress Sensitivity | `plotting/plugins/stress_sensitivity/stress_sens_plugin.py` + `plotting/stress_sensitivity/core.py` | _Removed_ | Old GUI exposes the PyPlot plugin for downstream callers. |
| VSM Hysteresis | `plotting/plugins/vsm_hysteresis/vsm_hysteresis_plugin.py` | `plotting/vsm_hysteresis_loops.py` | Already extracted into dedicated plugin module. |
| HSW Load Compare | `plotting/plugins/hsw_load_compare/{hsw_load_compare_plugin.py, core.py, dialog.py}` | Legacy scripts archived in `plotting/legacy/hsw_load_compare`. |
| Maxion Continuous | `plotting/plugins/maxion_continuous/{maxion_continuous_plugin.py, core.py, dialog.py}` | Plugin owns the workflow; reference copies live under `plotting/legacy/maxion_continuous`. |
| PDF Plotter | `plotting/plugins/pdf_plotter/{pdf_plotter_plugin.py, dialog.py}` | Plugin bundle contains the dialog; legacy GUI preserved in `plotting/legacy/pdf_plotter`. |
| Hysteresis Loops | `plotting/plugins/hysteresis_loops/{hysteresis_loops_plugin.py, core.py, dialog.py}` | Plugin package is authoritative; archived modules reside in `plotting/legacy/hysteresis_loops`. |
| HSW Distribution | `plotting/plugins/hsw_distribution/{hsw_distribution_plugin.py, dialog.py}` | Plugin is self-contained; historical GUI stored in `plotting/legacy/hsw_distribution`. |
| Strain 3D Plot | `plotting/plugins/strain_3d_plot/{strain_3d_plot_plugin.py, widget.py}` | Plugin hosts the widget; the legacy script is in `plotting/legacy/strain_3d_plot.py`. |

- All of the above modules register their `PyPlotPlugin` subclasses through the shared
  `plotting.plugins.base.register_plugin` decorator. The workbench queries the registry at runtime,
  and the launcher mirrors that list, so no manual wiring inside `plotting/pyplot/app.py` remains.

### 1.3 Legacy/Unplugged Components
- `plotting/legacy/` – archived compatibility shims and historical GUIs retained for reference; the active code paths now live exclusively inside `plotting/plugins/`.
- `plotting/pyplot.py`, `plotting/pyplot_app.py` – historical entry points preserved for reference.
- Tests/CLI: no CLI commitments required; launcher drives PyPlot directly.

### 1.4 Shared Assets & Resources
- `plotting/default_config.json` – shared defaults.
- Icons/QSS currently embedded inside legacy GUIs; no centralised resources folder yet.

## 2. Proposed Unified Layout

```
plotting/
  plugins/
    temperature_dependence/
      __init__.py
      temp_dep_plugin.py
      charts.py        # optional helpers
      resources/       # icons, ui snippets
    temperature_sensitivity/
      temp_sens_plugin.py
    current_annealing/
      current_annealing_plugin.py
    stress_dependence/
      stress_dep_plugin.py
    stress_sensitivity/
      stress_sens_plugin.py
    vsm_hysteresis/
      vsm_hysteresis_plugin.py
    hsw_distribution/
      __init__.py
      hsw_distribution_plugin.py
      dialog.py
    hsw_load_compare/
      __init__.py
      hsw_load_compare_plugin.py
      core.py
      dialog.py
    hysteresis_loops/
      __init__.py
      hysteresis_loops_plugin.py
      core.py
      dialog.py
    maxion_continuous/
      __init__.py
      maxion_continuous_plugin.py
      core.py
      dialog.py
    pdf_plotter/
      __init__.py
      pdf_plotter_plugin.py
      dialog.py
    strain_3d_plot/
      __init__.py
      strain_3d_plot_plugin.py
      widget.py
  shared/
    __init__.py
    utils.py
    backends.py
    config.py
    resources/
  pyplot/
    __init__.py
    window.py
    app.py
    console.py
  archived/
    temperature_dependence/
      temp_dep_gui.py
      README.md         # deprecation notice
    ...                 # one folder per legacy GUI
```

- Old modules re-export the new plugin classes until the archival phase completes.
- `archived/` acts as a safety net so external imports do not break mid-migration.
- No CLI tooling retained per request; PyPlot becomes the single entry point.

## 3. Next Migration Actions
1. Consolidate duplicated helper utilities (readability/formatting, backend preferences, resource loading) into `plotting/shared/` and repoint plugins away from the legacy dialogs.
2. Add deprecation stubs and notices inside the archived GUI modules once consumers finish switching to the new packages.
3. Expand automated coverage so plugin packages exercise load/plot/export flows (beyond the current toolbar smoke test).
