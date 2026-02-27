# AGENT GUIDELINES

## Environment
- Use a project virtual environment for all Python commands.
- Use whichever project virtual environment best matches the task/runtime (for example `.venv` for Windows/Origin integration, `.venv-wsl` for WSL-native work) and state which interpreter was used.

## Dependencies
- Edit `pyproject.toml` first, then update `requirements.txt` from it.
- Keep `pyproject.toml` and `requirements.txt` aligned; don't submit mismatched pins.
- If Windows-only dependencies change, also sync `requirements-win.txt`.

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
