# Agent Guidelines

## Dependency maintenance
- Edit pyproject.toml first, then run pip-compile --upgrade pyproject.toml so requirements.txt stays in sync.
- Keep pins aligned between pyproject and requirements; do not submit mismatched files.
- Document any compatibility-driven pin choices (for example, the OpenCV vs NumPy constraint) in the changelog.

## Changelog discipline
- Every user-facing change or dependency update needs a dated entry in CHANGELOG.md before delivery.
- Use ISO dates (YYYY-MM-DD) and concise bullets describing user impact.
- Call out migrations or runtime requirement changes explicitly.

## Testing and verification
- After dependency changes, sanity-check imports for key modules (PyQt6, matplotlib, numpy, pandas, plotly, opencv-python if used).
- Re-run smoke scripts such as launcher.py when practical, or note when they were not run.
