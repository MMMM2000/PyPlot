2026-03-10 09:14 UTC

- Simplified contributor guidance to use a single project `.venv` instead of a separate `.venv-wsl` workflow.
- Documented that Codex should create, refresh, or recreate `.venv` and reinstall dependencies when the environment is missing, stale, or on the wrong Python version.
- Clarified that Windows setups must install `requirements.txt` before layering `requirements-win.txt` on top.
