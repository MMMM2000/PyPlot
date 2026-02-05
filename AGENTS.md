# AGENT GUIDELINES

## Environment
- Use a project virtual environment for all Python commands.
- On WSL, prefer `.venv-wsl` if present; otherwise create/activate a local venv and state which interpreter was used.

## Dependencies
- Edit `pyproject.toml` first, then update `requirements.txt` from it.
- Keep `pyproject.toml` and `requirements.txt` aligned; don’t submit mismatched pins.
- If Windows-only dependencies change, also sync `requirements-win.txt`.

## Changelog
- Any user-facing change or dependency update must have a dated entry in `CHANGELOG.md`.
- Use UTC timestamps: `YYYY-MM-DD HH:MM`.
- Call out migrations or runtime requirement changes explicitly.

## Testing
- After dependency changes, sanity-check imports for key modules: PyQt6, matplotlib, numpy, pandas, plotly, opencv-python (if used).
- Run the most relevant tests for the touched area; if you can’t, state what wasn’t run.
- Run `launcher.py` as a smoke check when practical, or note if skipped.

## Diagnostics
- When investigating crashes or errors, check `logs/message_log.txt` (and other files under `logs/`) first and summarize relevant traces.

## Task Tracking
- Keep `TODO.md` updated based on the conversation.
- Ask before moving items to "Done."

## Origin Integration
- Consult the OriginLab Python docs when implementing Origin-related behavior:
  https://docs.originlab.com/originpro/index.html

## Docs
- When changing PyPlot or plug-ins, check `docs/pyplot.md` first and update it if you introduce new conventions or behavior.
