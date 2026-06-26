# PyPlot Migration Tracker

## 1. Inventory & Dependencies

### 1.1 Core PyPlot Runtime
- `plotting/pyplot/window.py` â€“ main QMainWindow subclass, shared widgets, graph state helpers.
- `plotting/pyplot/app.py` â€“ workbench orchestration, plugin registration, project persistence.
- `plotting/pyplot/console.py`, `plotting/python_console.py` â€“ embedded Python console.
- Shared helpers: `plotting/shared/toolkit.py`, `plotting/shared/common.py`, `plotting/shared/backends.py`, `plotting/shared/config.py`, `plotting/shared/__init__.py`.

### 1.2 Active Plugins (wired through `PyPlotWorkbench`)
| Plugin name | Source module(s) | Legacy status | Notes |
|-------------|------------------|---------------|-------|
| Temperature Dependence | `plotting/plugins/temperature_dependence/{temp_dep_plugin.py, core.py}` | Compatibility shims removed 2025-11-07 | TXT export still routed through shared helper. |
| Temperature Sensitivity | `plotting/plugins/temperature_sensitivity/{temp_sens_plugin.py, core.py}` | Compatibility shims removed 2025-11-07 | Legacy dialog fully retired. |
| Current Annealing | `plotting/plugins/current_annealing/{current_annealing_plugin.py, core.py, burnthrough.py}` | Compatibility shims removed 2025-11-07 | Uses `format_annealing_title`. |
| Stress Dependence | `plotting/plugins/stress_dependence/{stress_dep_plugin.py, core.py}` | Compatibility shims removed 2025-11-07 | Console helpers live with the plugin. |
| Stress Sensitivity | `plotting/plugins/stress_sensitivity/{stress_sens_plugin.py, core.py}` | Compatibility shims removed 2025-11-07 | Plugin hosts both GUI and helpers. |
| VSM Hysteresis | `plotting/plugins/vsm_hysteresis/{vsm_hysteresis_plugin.py, vsm_hysteresis_loops.py}` | Compatibility shims removed 2025-11-07 | Plugin wraps the relocated `VSMPlotter`. |
| HSW Load Compare | `plotting/plugins/hsw_load_compare/{hsw_load_compare_plugin.py, core.py, dialog.py}` | Legacy dialogs removed 2025-11-07 | |
| Maxion Continuous | `plotting/plugins/maxion_continuous/{maxion_continuous_plugin.py, core.py, dialog.py}` | Legacy dialogs removed 2025-11-07 | |
| PDF Plotter | `plotting/plugins/pdf_plotter/{pdf_plotter_plugin.py, dialog.py}` | Legacy dialogs removed 2025-11-07 | |
| Hysteresis Loops | `plotting/plugins/hysteresis_loops/{hysteresis_loops_plugin.py, core.py, dialog.py}` | Legacy dialogs removed 2025-11-07 | |
| HSW Distribution | `plotting/plugins/hsw_distribution/{hsw_distribution_plugin.py, dialog.py}` | Legacy dialogs removed 2025-11-07 | |
| Strain 3D Plot | `plotting/plugins/strain_3d_plot/{strain_3d_plot_plugin.py, widget.py}` | Legacy script removed 2025-11-07 | |

- All of the above modules register their `PyPlotPlugin` subclasses through the shared
  `plotting.plugins.base.register_plugin` decorator. The workbench queries the registry at runtime,
  and the launcher mirrors that list, so no manual wiring inside `plotting/pyplot/app.py` remains.

### 1.3 Legacy/Unplugged Components
- `plotting/legacy/` â€“ **removed 2025-11-07**; all compatibility shims and historical GUIs have been deleted after migration verification.
- `plotting/pyplot.py` â€“ historical entry point preserved for reference; `plotting/pyplot_app.py` retired with the legacy package.
- Tests/CLI: no CLI commitments required; launcher drives PyPlot directly.

### 1.4 Shared Assets & Resources
- `plotting/default_config.json` â€“ shared defaults.
- Icons/QSS currently embedded inside legacy GUIs; no centralised resources folder yet.

## 2. Unified Layout (Post-Migration)

```
plotting/
  plugins/
    current_annealing/
      __init__.py
      burnthrough.py
      core.py
      current_annealing_plugin.py
    temperature_dependence/
      __init__.py
      core.py
      temp_dep_plugin.py
    temperature_sensitivity/
      __init__.py
      core.py
      temp_sens_plugin.py
    stress_dependence/
      __init__.py
      core.py
      stress_dep_plugin.py
    stress_sensitivity/
      __init__.py
      core.py
      stress_sens_plugin.py
    vsm_hysteresis/
      __init__.py
      vsm_hysteresis_loops.py
      vsm_hysteresis_plugin.py
    ... (other plugins unchanged)
  shared/
    ...
  pyplot/
    ...
```

- All compatibility shims have been removed; importing from `plotting.plugins.*` is required.
- PyPlot remains the single entry point; CLI scripts are wrappers over the plugin workbench.

## 3. Next Migration Actions
1. Monitor deprecation warnings and update downstream callers to the new plugin import paths.
2. Continue consolidating shared helpers (readability/formatting, backend preferences) where duplication still exists across plugins.
3. Expand automated coverage so plugin packages exercise load/plot/export flows (beyond the current toolbar smoke test).
