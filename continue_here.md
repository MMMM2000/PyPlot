# Continue Here: PyPlot uv Migration

Date: 2026-05-24
Branch: `codex/move-pyplot-to-uv`

## Current State

- The Mac side is done and clean.
- `uv.lock` is present and checked.
- `pyproject.toml`, `requirements.txt`, README/setup guidance, AGENTS guidance, and Codex `.codex/environments/environment.toml` have been migrated to a uv-primary workflow.
- `requirements.txt` and `requirements-win.txt` are still kept as compatibility exports/fallbacks.
- Codex environment setup now uses `uv sync --extra test` instead of manual `.venv` + pip installs.

## Mac Verification Already Done

On macOS, this passed:

```sh
UV_CACHE_DIR=artifacts/uv-cache uv run python --version
UV_CACHE_DIR=artifacts/uv-cache uv lock --check
```

Result:

```text
Python 3.14.3
Resolved 53 packages
```

Earlier full verification also passed:

```sh
uv run python -m pip check
uv run python launcher.py --help
uv run pytest tests
```

The full suite result was:

```text
879 passed, 21 skipped
```

## Finish On Windows

The remaining important validation is Windows, because that is where PyPlot has the more fragile setup story: Python launcher, non-ASCII paths, PyQt, Origin packages, and Codex worktree setup.

Run these from the PyPlot repo root on the Windows laptop:

```powershell
uv --version
py -0p
uv sync --extra test
uv run python --version
uv run python launcher.py --help
uv run pytest tests
```

Confirm that:

- `uv` is installed and available in PowerShell.
- `py -0p` lists Python 3.14.
- `uv run python --version` reports Python 3.14.x.
- `uv sync --extra test` creates/updates `.venv` without falling back to Python 3.13.
- `launcher.py --help` works.
- Tests pass, or any failures are real Windows-specific issues worth fixing.

## If uv Is Missing On Windows

Install it with:

```powershell
winget install --id=astral-sh.uv -e
```

Then restart PowerShell and rerun:

```powershell
uv --version
```

## If Python 3.14 Is Missing On Windows

Install/register Python 3.14 first. Do not let this repo run under Python 3.13.

Check with:

```powershell
py -0p
```

Then retry:

```powershell
uv sync --extra test
```

## Useful Cache/Temp Pattern For Windows

If Windows temp/cache permissions or non-ASCII path issues show up, run with workspace-scoped paths:

```powershell
$env:TEMP = "artifacts\tool-temp"
$env:TMP = "artifacts\tool-temp"
$env:UV_CACHE_DIR = "artifacts\uv-cache"
$env:PIP_CACHE_DIR = "artifacts\pip-cache"
New-Item -ItemType Directory -Force $env:TEMP, $env:UV_CACHE_DIR, $env:PIP_CACHE_DIR | Out-Null
uv sync --extra test
```

## Definition Of Done

This migration is done when Windows confirms:

- `uv sync --extra test` succeeds.
- Python is 3.14.x.
- `launcher.py --help` succeeds.
- The test suite passes or any Windows-only failures are documented and fixed.

