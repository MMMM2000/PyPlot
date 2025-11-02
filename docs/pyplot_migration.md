# PyPlot Migration Tracker

## 1. Inventory & Dependencies

### 1.1 Core PyPlot Runtime
- `plotting/pyplot/window.py` – main QMainWindow subclass, shared widgets, graph state helpers.
- `plotting/pyplot/app.py` – workbench orchestration, plugin registration, project persistence.
- `plotting/pyplot/console.py`, `plotting/python_console.py` – embedded Python console.
- Shared helpers: `plotting/utils.py`, `plotting/common.py`, `plotting/backends.py`, `plotting/config.py`, `plotting/shared/__init__.py`.

### 1.2 Active Plugins (wired through `PyPlotWorkbench`)
| Plugin name | Source module(s) | Legacy entry points | Notes |
|-------------|------------------|---------------------|-------|
| Temperature Dependence | `plotting/plugins/temperature_dependence/temp_dep_plugin.py` + `plotting/temperature_dependence/core.py` | `plotting/temperature_dependence/temp_dep_gui.py` | TXT export still routed through legacy helper; GUI re-exports the plugin class. |
| Temperature Sensitivity | `plotting/plugins/temperature_sensitivity/temp_sens_plugin.py` + `plotting/temperature_sensitivity/core.py` | `plotting/temperature_sensitivity/temp_gui.py` | Legacy dialog now shims to the PyPlot plugin. |
| Current Annealing | `plotting/plugins/current_annealing/current_annealing_plugin.py` + `plotting/current_annealing/core.py` | `plotting/current_annealing/anneal_gui.py` | Uses `format_annealing_title`; compatibility wrapper exports the plugin. |
| Stress Dependence | `plotting/plugins/stress_dependence/stress_dep_plugin.py` + `plotting/stress_dependence/core.py` | `plotting/stress_dependence/stress_gui.py` | Legacy GUI still imports console helpers; now re-exports the plugin class. |
| Stress Sensitivity | `plotting/plugins/stress_sensitivity/stress_sens_plugin.py` + `plotting/stress_sensitivity/core.py` | `plotting/stress_sensitivity/sens_gui.py` | Old GUI exposes the PyPlot plugin for downstream callers. |
| VSM Hysteresis | `plotting/plugins/vsm_hysteresis/vsm_hysteresis_plugin.py` | `plotting/vsm_hysteresis_loops.py` | Already extracted into dedicated plugin module. |
| HSW Load Compare | `plotting/plugins/hsw_load_compare/hsw_load_compare_plugin.py` + `plotting/hsw_load_compare/load_compare_gui.py` | Same module hosts embedded widget. |
| Maxion Continuous | `plotting/plugins/maxion_continuous/maxion_continuous_plugin.py` + `plotting/maxion_continuous/maxion_gui.py` | Embedded widget host with compatibility GUI. |
| PDF Plotter | `plotting/plugins/pdf_plotter/pdf_plotter_plugin.py` + `plotting/pdf_plotter/pdf_gui.py` | Standalone dialog now wraps the plugin export. |
| Hysteresis Loops | `plotting/plugins/hysteresis_loops/hysteresis_loops_plugin.py` + `plotting/hysteresis_loops/loops_gui.py` | Embedded widget plugin. |
| HSW Distribution | `plotting/plugins/hsw_distribution/hsw_distribution_plugin.py` + `plotting/hsw_distribution/distribution_gui.py` | Legacy dialog remains; plugin exported for compatibility. |
| Strain 3D Plot | `plotting/plugins/strain_3d_plot/strain_3d_plot_plugin.py` + `plotting/strain_3d_plot.py` | Embedded widget plugin. |

### 1.3 Legacy/Unplugged Components
- `plotting/*/*_gui.py` – legacy Qt dialogs for each workflow (now redundant but still bundled).
- `plotting/pyplot.py`, `plotting/pyplot_app.py` – historical entry points preserved for reference.
- `plotting/legacy/` – placeholder package for historical code (currently empty).
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
      hsw_distribution_plugin.py
    hsw_load_compare/
      hsw_load_compare_plugin.py
    hysteresis_loops/
      hysteresis_loops_plugin.py
    maxion_continuous/
      maxion_continuous_plugin.py
    pdf_plotter/
      pdf_plotter_plugin.py
    strain_3d_plot/
      strain_3d_plot_plugin.py
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
