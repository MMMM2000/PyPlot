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

Run verification locally in Codex on Windows or from PowerShell. Install the
locked test environment first with `uv sync --locked --extra test`.

```powershell
.\scripts\run_tests.ps1 -Mode software -JunitDir artifacts\verification-reports
.\scripts\run_tests.ps1 -Mode focused tests\test_ci_verify.py
.\scripts\run_tests.ps1 -Mode full -JunitDir artifacts\full-verification-reports
.\scripts\run_tests.ps1 -Mode full -Workers 0
```

The equivalent Python entrypoint works when PowerShell script execution is
restricted:

```powershell
uv run --locked --extra test python scripts/ci_verify.py --mode software --junit-dir artifacts/verification-reports
uv run --locked --extra test python scripts/ci_verify.py --mode focused tests/test_ci_verify.py
```

`software` runs a representative set with two workers plus a separate serial
lane: runner/isolation checks, synthetic parser fixtures, numerical scan handling,
package/codec integrity, Builder update integrity, fake power-supply checks,
process contracts, configuration and offscreen GUI smoke checks. This selection
uses small synthetic fixtures independently of `sample_data/` and Drive.
`full` selects the complete tests directory and defaults to four workers.
`focused` runs supplied targets in one process, or just one launcher smoke test
when no targets are supplied. Use `-DryRun` / `--dry-run` to inspect the selection.

The runner forces offscreen Qt and Agg, keeps settings and Builder storage
private to each run, and gives each xdist worker private temp, Qt settings,
Matplotlib and Builder paths. Tests marked `serial` run afterward in a separate
process. Console output includes skip reasons (`-ra`); `-JunitDir` / `--junit-dir`
writes separate lane XML reports. Failures and runs with no selected tests return
a failure exit code; an empty companion lane is allowed.

Windows temp and pytest basetemp default to per-run folders under `artifacts/t/`;
settings and storage stay under `artifacts/test-runs/`. The runner checks
create/write/read/rename/delete access before testing and reports the failing
path without falling back to a shared directory. Windows basetemp retains 8.3
aliases where available and uses an extended-length path for deep fixtures,
without changing machine permissions or Windows long-path settings. Libraries
that discard that prefix may still need a shorter writable ASCII parent: use
`-TempRoot <path>`, `--temp-root <path>`, or `PYPLOT_VERIFY_TEMP_ROOT` (CLI wins).
The runner prints the resolved path length and warns above 60 characters as a
conservative diagnostic. It appends its own private directory; do not override
pytest's `--basetemp`.

Use a unique `--run-id` for concurrent runs. Supplied ids must be 1-48 ASCII
letters, digits, hyphens or underscores, starting with a letter or digit.
Reusing an id lets pytest replace that run's disposable lane directories. Keep
reports under ignored `artifacts/` and preserve evidence until review.

Verification is run within Codex sessions or local shells; this change installs
no GitHub Actions workflow and consumes no Actions minutes. Choose additional
checks for the changed feature. Full-suite verification and live Origin/Office
or hardware acceptance remain separate. Use `--workers 0` to diagnose ordering
problems; `--dist` controls parallel distribution. Windows defaults to `load`
to preserve the Qt test collection order within each worker; other platforms
default to `worksteal`.
