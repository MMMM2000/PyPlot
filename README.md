# Microwire Data Plotting & Logging

A compact toolkit for logging, visualising, and post-processing microwire
experiments. The launcher keeps the utilities together so you can jump between
loggers, plotters, emulators, and builders without starting individual scripts.

## Quick Start

1. Install Python 3.14.x. The project currently declares
   `requires-python = ">=3.14,<3.15"` in `pyproject.toml`; 3.14.4 is the current reference build.
2. Install uv if it is not already available:
   - macOS/Linux: `curl -LsSf https://astral.sh/uv/install.sh | sh`
   - Windows PowerShell:
     `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`
     or `winget install --id=astral-sh.uv -e`
3. Sync the project environment:
   - Runtime only: `uv sync`
   - Runtime plus tests: `uv sync --extra test`
4. Launch the hub: `uv run python -m launcher`

> **Tip:** `uv sync` creates and updates the project `.venv` from `pyproject.toml`
> and `uv.lock`. If the environment was created with the wrong Python minor
> version or has stale dependencies, rerun `uv sync --extra test`; uv will repair
> the disposable `.venv` when Python 3.14 is available.

> **Tip:** On Windows, run `py -0p` first if setup fails; Python 3.14 must be
> listed there before a Codex/worktree setup can install the project. If the
> Windows launcher has not registered 3.14 yet, install or repair the Python 3.14
> per-user installation before running `uv sync`.

**pip compatibility:** `pyproject.toml` is the source of truth and `uv.lock` is
the preferred lock. `requirements.txt` and `requirements-win.txt` remain exported
compatibility files for machines or packaging scripts that still use pip. For the
fallback path, create `.venv` with Python 3.14, activate it, upgrade pip, install
`requirements.txt`, and on Windows install `requirements-win.txt` afterwards.

OriginPro users should also install `originpro`, `numpy`, `pandas`,
`python-dateutil`, `pytz`, `six`, and `tzdata` inside Origin's embedded Python
before choosing the Origin backend.

## Tools

- Master Launcher - browse and start every utility from one window with search
  and recent-history sorting.
- VSM Hysteresis Loops - plot loops by temperature/angle, align endpoints,
  export to Matplotlib figures or Origin workbooks.
- Stress Dependence/Sensitivity - analyse stress workflows from PyPlot with
  Matplotlib previews, Origin automation, and TXT exports for archival.
- Temperature Sensitivity - generate zeroed or raw temperature curves with
  optional continuous sweeps, then export to Origin or TXT directly from PyPlot.
- Serial Data Logger - capture instrument output with live plots and flexible
  filename templates.
- Mini DMA Logger - drive a small stepper stage, serial scale, and current-annealing
  supply with preload-aware strain zeroing, `.pydpj` specimen import, configurable
  multi-axis plot tiles, named TXT/CSV/JSON sessions, displacement-controlled
  ramp/cycle/hold recipes, and early Hsw distribution plateau sweeps in load,
  stress, or strain units.
- Microwire Data Builder - combine fabrication spreadsheets and annealing logs
  into an analysis-ready table.
- Universal Serial Emulator - spin up loopback serial pairs to exercise loggers
  without hardware.

## Building

Create a standalone application with PyInstaller after syncing the project
environment (`uv sync`):

```bash
uv run pyinstaller launcher.spec
```

The build appears under `dist/launcher`; zip that folder when sharing the tools.

## Development

- This repository is developed with Codex sessions.
- Detailed change history lives in `CHANGELOG.md`.
- PyPlot workbench plugins now live under `plotting/plugins/`; add new plotters there so they can be loaded without growing `plotting/pyplot/app.py`.
- Register new PyPlot plugins with the `@register_plugin("Display Name")` decorator found in
  `plotting/plugins/base.py`. The workbench and launcher discover plugin classes from the shared
  registry automatically, while standalone plotters can still be surfaced by handing their launch
  functions to `plotting.pyplot.app.main()`.

### Windows Verification

Use the Windows runner when invoking pytest from Codex or a local PowerShell
session. It forces headless Qt/Matplotlib defaults, stores pytest temp files and
Microwire Data Builder state under `artifacts/test-runs/`, and keeps tool caches
inside the workspace:

```powershell
.\scripts\run_tests.ps1 -Mode focused tests\test_launcher.py::test_launcher_detects_pyplot_automation_flags
.\scripts\run_tests.ps1 -Mode full
.\scripts\run_tests.ps1 -Mode focused -DryRun tests\test_launcher.py
```

If local execution policy blocks `.ps1` scripts, invoke the same runner with:

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\run_tests.ps1 -Mode focused -DryRun
```

The underlying Python entrypoint is also available for CI-like callers:

```powershell
uv run python scripts\ci_verify.py --mode focused --dry-run tests\test_launcher.py
uv run python scripts\ci_verify.py --mode full
```
