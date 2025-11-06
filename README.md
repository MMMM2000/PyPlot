# Microwire Data Plotting & Logging

A compact toolkit for logging, visualising, and post-processing microwire
experiments. The launcher keeps the utilities together so you can jump between
loggers, plotters, emulators, and builders without starting individual scripts.

## Quick Start

1. Install Python 3.13.x (we pin dependencies with Python 3.13; 3.13.9 is the current reference build).
2. Create a virtual environment with that interpreter:
   `python3.13 -m venv .venv` (macOS/Linux) or `py -3.13 -m venv .venv` (Windows)
3. Activate it (`source .venv/bin/activate` on macOS/Linux or
   `.venv\Scripts\activate` on Windows)
4. Upgrade pip: `python -m pip install --upgrade pip`
5. Install the runtime stack for every non-experiment tool:
   - macOS/Linux: `pip install -r requirements.txt`
   - Windows (bundles Origin automation wheels):
     `pip install -r requirements-win.txt`
6. (Optional) Install experiment helpers and test tooling:
   `pip install '.[test]'`
7. Launch the hub: `python -m launcher`

> **Tip:** The `pip install -r requirements.txt` command pulls in every
> dependency required to run the launcher and builder tools. Run `pip install '.[test]'`
> afterwards if you also plan to execute the bundled tests or
> experiment scripts.

**Windows-only Origin exports:** Installing from `requirements-win.txt` pulls in the
Origin automation wheels alongside the shared dependency lock, keeping PyInstaller
builds Origin-ready without a follow-up `pip install`. The wheels remain excluded from
the cross-platform `requirements.txt` because they are not published for macOS/Linux.

OriginPro users should also install `originpro`, `numpy`, `pandas`,
`python-dateutil`, `pytz`, `six`, and `tzdata` inside Origin’s embedded Python
before choosing the Origin backend.

## Tools

- Master Launcher – browse and start every utility from one window with search
  and recent-history sorting.
- VSM Hysteresis Loops – plot loops by temperature/angle, align endpoints,
  export to Matplotlib figures or Origin workbooks.
- Stress Dependence/Sensitivity – analyse stress workflows from PyPlot with
  Matplotlib previews, Origin automation, and TXT exports for archival.
- Temperature Sensitivity – generate zeroed or raw temperature curves with
  optional continuous sweeps, then export to Origin or TXT directly from PyPlot.
- Serial Data Logger – capture instrument output with live plots and flexible
  filename templates.
- Microwire Data Builder – combine fabrication spreadsheets and annealing logs
  into an analysis-ready table.
- Universal Serial Emulator – spin up loopback serial pairs to exercise loggers
  without hardware.

## Building

Create a standalone application with PyInstaller after installing the project
dependencies (`pip install -r requirements-win.txt` on Windows, `pip install -r
requirements.txt` on macOS/Linux):

```bash
pyinstaller launcher.spec
```

The build appears under `dist/launcher`; zip that folder when sharing the tools.

## Development

- This repository is developed exclusively with Codex CLI sessions
  (`npm install -g @openai/codex`).
- Detailed change history lives in `CHANGELOG.md`.
- PyPlot workbench plugins now live under `plotting/plugins/`; add new plotters there so they can be loaded without growing `plotting/pyplot/app.py`.
- Register new PyPlot plugins with the `@register_plugin("Display Name")` decorator found in
  `plotting/plugins/base.py`. The workbench and launcher discover plugin classes from the shared
  registry automatically, while standalone plotters can still be surfaced by handing their launch
  functions to `plotting.pyplot.app.main()`.
