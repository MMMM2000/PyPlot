# AGENT GUIDELINES

## Environment
- On WSL, prefer the pre-provisioned virtualenv at `.venv-wsl` (e.g. `/mnt/c/Users/Martin/PyPlot/.venv-wsl/bin/python`) for Python/pip/pytest instead of system interpreters.

## Dependency Maintenance
- Edit `pyproject.toml` first, then run `pip-compile --upgrade pyproject.toml` so `requirements.txt` stays in sync.
- Keep pins aligned between `pyproject.toml` and `requirements.txt`; do not submit mismatched files.
- Document any compatibility-driven pin choices (for example, the OpenCV vs NumPy constraint) in the changelog.

## Changelog Discipline
- Every user-facing change or dependency update needs a dated entry in `CHANGELOG.md` before delivery.
- Use ISO timestamps (`YYYY-MM-DD HH:MM` in 24-hour UTC) and concise bullets describing user impact.
- Call out migrations or runtime requirement changes explicitly.

## Testing and Verification
- After dependency changes, sanity-check imports for key modules (PyQt6, matplotlib, numpy, pandas, plotly, opencv-python if used).
- Re-run smoke scripts such as `launcher.py` when practical, or note when they were not run.
- Keep running tests and iterating until each feature is fully functional; create new tests or extend existing ones whenever coverage is missing.

## Diagnostics
- When investigating crashes or errors, check `logs/message_log.txt` (and other files under `logs/`) first and summarize any relevant stack traces or warnings.

## Task Tracking
- Keep `TODO.md` up to date based on conversations; add new tasks yourself and ask the user before moving items to "Done."

## Origin Integration
- Prefer the shipped `origin_ext_python/originpro-main` tree (or configure `PYTHONPATH` to include an Origin Python SDK) whenever touching Origin exports or automation helpers.
- Consult the OriginLab Python documentation at https://docs.originlab.com/originpro/index.html for missing APIs or behavior samples before adding duplicate logic.

## PyPlot Updates
- When changing PyPlot or any of its plug-ins, review `docs/pyplot.md` first and keep it updated with any new conventions or behavioral rules you introduce.
