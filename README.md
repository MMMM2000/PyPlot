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
- TMA Logger - drive a small stepper stage, serial scale, and current-annealing
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

Install the locked test environment with `uv sync --locked --extra test`, then
use the Windows runner from Codex or PowerShell. It forces offscreen Qt and Agg,
keeps settings and Builder storage private to each run, and gives each xdist
worker private temp, Qt settings, Matplotlib and Builder paths. Tests marked
`serial` run afterward in a separate process. Full mode defaults to four
workers; focused mode defaults to one process and, without targets, runs only
one launcher smoke test.

```powershell
.\scripts\run_tests.ps1 -Mode focused tests\test_ci_verify.py
.\scripts\run_tests.ps1 -Mode full -JunitDir artifacts\verification-reports
.\scripts\run_tests.ps1 -Mode full -Workers 0
.\scripts\run_tests.ps1 -Mode focused -DryRun tests\test_launcher.py
```

The equivalent Python entrypoint works when PowerShell script execution is
restricted:

```powershell
uv run --locked --extra test python scripts/ci_verify.py --mode focused tests/test_ci_verify.py
uv run --locked --extra test python scripts/ci_verify.py --mode full --junit-dir artifacts/verification-reports
```

Windows temp and pytest basetemp now default to short per-run directories under
`artifacts/t/`; settings and storage stay under `artifacts/test-runs/`. The runner
checks create/write/read/rename/delete access before starting pytest and reports
the failing path without falling back to a shared directory. It prints the
resolved basetemp length and retains Windows short aliases where available.
Deep fixtures may still need a shorter checkout or an explicitly chosen writable
ASCII parent: use `-TempRoot <path>`, `--temp-root <path>`, or
`PYPLOT_VERIFY_TEMP_ROOT` (the command-line option takes precedence). The runner
appends its own private run directory; do not override pytest's `--basetemp`.
Use a unique `--run-id` for concurrent runs; supplied ids must be 1-48 ASCII
letters, digits, hyphens or underscores, starting with a letter or digit.
Reusing an id allows pytest to replace that run's disposable lane directories.

The [Windows PR workflow](.github/workflows/windows-verification.yml) runs on
`windows-2025`, selects Python from `pyproject.toml`, installs locked dependencies,
and runs an explicit software gate with two workers plus the serial lane. Its
PowerShell run block can also be copied into a local session to reproduce the
same target list. The gate covers runner failures and worker isolation, synthetic
parser fixtures, numerical scan handling, package/codec integrity, Builder update
integrity, fake power-supply checks, process contracts, configuration and offscreen
GUI smoke checks. It does not use the large `sample_data/` tree.

The workflow has a 15-minute limit, cancels superseded runs for the same PR, and
retains only its console log and separate lane JUnit XML reports for seven days.
Skip reasons appear in the console (`-ra`) and XML. Failures and runs with no
selected tests fail the job; an empty companion lane is allowed. Full-suite
verification and live Origin/Office or hardware acceptance remain separate.
Review hosted results before making
it a required merge check. Use `--workers 0` to diagnose ordering problems;
`--dist` controls the parallel distribution strategy (default `worksteal`).
