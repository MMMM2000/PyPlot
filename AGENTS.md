# AGENT GUIDELINES

## Environment
- Use a project virtual environment for all Python commands.
- Use the project `.venv` and state which interpreter was used.
- If `.venv` is missing, outdated, or tied to the wrong Python minor version for the project, create or recreate it before running Python commands.
- Treat `.venv` as disposable generated state: if the environment is broken or the project now requires a newer Python version, delete the old `.venv` and build a fresh one instead of waiting for the user to do it.

## Dependencies
- Edit `pyproject.toml` first, then update `requirements.txt` from it.
- Keep `pyproject.toml` and `requirements.txt` aligned; don't submit mismatched pins.
- If Windows-only dependencies change, also sync `requirements-win.txt`.
- When the current environment is missing dependencies or has stale pins, update it yourself. On Windows install `requirements.txt` first and then `requirements-win.txt`; otherwise install `requirements.txt`.
- Install `.[test]` when tests or test-only tools are needed.

## Git Sync
- Before starting substantive work, refresh remote state with `git fetch --all --prune`.
- If the current branch tracks an upstream, check whether it is behind before making changes.
- When the worktree is clean and the update is a fast-forward, bring the branch up to date with `git pull --ff-only`.
- If there are local changes, no upstream branch, or the pull would require a merge or rebase, stop and summarize the situation instead of forcing a sync.

## Changelog
- Keep `CHANGELOG.md` as the canonical release history on `main`.
- In feature branches/worktrees, add a changelog fragment under `changelog.d/` instead of editing `CHANGELOG.md` directly.
- Fragment format: start with a UTC timestamp `YYYY-MM-DD HH:MM`, then a concise bullet list of user-facing changes.
- Call out migrations or runtime requirement changes explicitly.

## Testing
- After dependency changes, sanity-check imports for key modules: PyQt6, matplotlib, numpy, pandas, plotly, opencv-python (if used).
- Run the most relevant tests for the touched area; if you can't, state what wasn't run.
- Run `launcher.py` as a smoke check when practical, or note if skipped.
- For graph-generation changes, always create real output graphs and visually verify results before finishing.
- Apply that visual verification rule to both Matplotlib graphs and Origin-exported graphs, and continue iterating until output is correct.
- Store temporary visual-check outputs under a workspace `artifacts/` folder (create it when needed).
- Remove temporary PNG artifacts and other transient visual-check files after validation, then remove `artifacts/` when it only contains those temporary checks.
- Remove temporary visual-check artifacts left in other locations (for example probe dumps, one-off summaries, and debug folders in `logs/`) once validation is complete.

## Diagnostics
- When investigating crashes or errors, check `logs/message_log.txt` (and other files under `logs/`) first and summarize relevant traces.

## Origin Integration
- Consult the OriginLab Python docs when implementing Origin-related behavior:
  https://docs.originlab.com/originpro/index.html

## Docs
- When changing PyPlot or plug-ins, check `docs/pyplot.md` first and update it if you introduce new conventions or behavior.
