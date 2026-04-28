# AGENT GUIDELINES

## Environment
- Use the project virtual environment for Python commands.
- Prefer the project `.venv`; on Windows use `.\.venv\Scripts\python.exe` explicitly and state which interpreter was used in the final summary.
- If `.venv` is missing, broken, or tied to the wrong Python minor version for the project, recreate it before running project Python commands.
- Treat `.venv` as disposable generated state. If the project now requires a newer Python version, replace the old `.venv` instead of asking the user to clean it up.
- On Windows accounts with non-ASCII user paths, expect tool temp/cache issues. When running `pip`, `pip-compile`, or pytest, prefer workspace-scoped ignored paths such as `artifacts/tool-temp`, `artifacts/pip-cache`, and `artifacts/pip-tools-cache` for `TEMP`, `TMP`, `PIP_CACHE_DIR`, and pip-tools `--cache-dir`.

## Dependencies
- Edit `pyproject.toml` first, then regenerate `requirements.txt` from it.
- Keep `pyproject.toml`, `requirements.txt`, and generated lock headers aligned with the Python version used to compile them.
- If Windows-only dependencies change, also sync `requirements-win.txt`.
- On Windows install `requirements.txt` first, then `requirements-win.txt`; otherwise install `requirements.txt`.
- Install `.[test]` when tests or test-only tools are needed.
- If a clean venv exposes a direct import that was previously only present by accident, add it to `pyproject.toml` and regenerate the lock file.

## Git Sync
- Before substantive work, run `git fetch --all --prune`.
- If the current branch tracks an upstream, check whether it is behind before making changes.
- When the worktree is clean and the tracked branch can fast-forward, update with `git pull --ff-only`.
- If there are local changes, no upstream branch, or the update would require merge/rebase, do not force a sync. State the situation and continue when the user intentionally created or selected the current branch.

## Changelog
- Keep `CHANGELOG.md` as the canonical release history on `main`.
- In feature branches/worktrees, add a changelog fragment under `changelog.d/` instead of editing `CHANGELOG.md` directly.
- Fragment format: start with a UTC timestamp `YYYY-MM-DD HH:MM`, then a concise bullet list of user-facing changes.
- Call out migrations, runtime requirement changes, dependency upgrades, and notable compatibility fixes explicitly.

## Testing
- After dependency changes, sanity-check imports for the relevant stack. At minimum for core runtime changes check PyQt6, matplotlib, numpy, pandas, scipy, and plotly; also check Origin/PDF packages or `cv2` when those dependencies are declared or touched.
- Run the most relevant tests for the touched area. If the full suite is too slow or blocked by environment issues, run targeted suites and state what was not run.
- For Qt tests in headless shells, set `QT_QPA_PLATFORM=offscreen`; for Matplotlib-heavy tests, set `MPLBACKEND=Agg`.
- Prefer `pytest tests` or targeted test paths over bare `pytest` when repo-root transient folders might be collected.
- If pytest cache/temp creation causes Windows access-denied errors, rerun with a fresh workspace temp directory and consider `-p no:cacheprovider`. Report any transient directories that Windows refuses to remove after best-effort cleanup.
- Run `launcher.py --help` as the default smoke check for runtime/dependency changes. Run a fuller launcher or GUI smoke check when the touched behavior needs it, or state why it was skipped.
- Never run verification directly against the user's real `.pypj` or `.pydpj` project files. Always make a disposable copy first and test against the copy.
- If tests need Microwire Data Builder storage, isolate app-data stores with a temporary storage root; do not write to the user's real `.microwire_data_builder` data.

## Visual Verification
- For graph-generation changes, create real output graphs and visually verify the results before finishing.
- Apply visual verification to Matplotlib outputs always when graph rendering changes. Apply it to Origin-exported graphs when Origin is available and the change touches Origin behavior.
- Continue iterating until the generated visual output matches the intended behavior, or clearly report the blocker.
- When a task changes visible PyPlot or Microwire Data Builder UI behavior and the result is meant for user review, capture final review screenshots after verification.
- For PyPlot or Microwire Data Builder GUI screenshots, use a fullscreen or clearly maximized window layout so docks, toolbars, tables, preview panels, and graph content are readable.
- By default, show final review screenshots inline in chat using absolute local image paths. Save screenshots to `~/Downloads` only when the user asks for saved files there, asks for a path, or inline screenshots alone would be insufficient.

## Artifacts And Cleanup
- Store temporary visual-check, pytest, pip, and diagnostic outputs under ignored workspace folders, preferably `artifacts/`.
- Remove temporary PNGs, probe dumps, one-off summaries, debug folders, and tool caches after validation when they are no longer needed.
- Cleanup is best effort on Windows. If a generated temp directory cannot be removed because of permissions or a live handle, summarize the path and what cleanup was attempted.
- Do not remove user data, real project files, or persistent app-data caches unless the user explicitly asks for that cleanup and the target path has been verified.

## Diagnostics
- When investigating crashes or errors, check `logs/message_log.txt` and other files under `logs/` first, then summarize relevant traces.
- For path-default or cache-home problems, inspect process-wide environment changes as well as the immediate UI code; `HOME`, `USERPROFILE`, and tool-specific cache variables can affect unrelated dialogs.

## Origin Integration
- Consult the OriginLab Python docs when implementing or changing Origin-related behavior:
  https://docs.originlab.com/originpro/index.html
- Treat Origin validation as Windows- and installation-dependent. If Origin or its automation runtime is unavailable, run the non-Origin checks and state that Origin verification was skipped.

## Docs
- When changing user-facing PyPlot or plugin behavior, workflows, project formats, or conventions, check the relevant docs such as `docs/pyplot.md` and update them when the behavior changes.
- Pure dependency bumps, internal test fixes, or narrow compatibility guards usually do not need docs unless they change setup requirements or user-visible behavior.
