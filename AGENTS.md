# AGENT GUIDELINES

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
