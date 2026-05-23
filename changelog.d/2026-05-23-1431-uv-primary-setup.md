2026-05-23 14:31 UTC

- Added `uv.lock` and made uv the preferred environment sync path for PyPlot development and Codex worktrees.
- Kept `requirements.txt` and `requirements-win.txt` as pip compatibility exports for machines and packaging scripts that still need them.
- Pinned the PyQt Qt runtime packages explicitly so uv-created environments keep the tested PyQt6/Qt runtime pairing.
- Added a Microwire Data Builder storage-root override for tests so automated runs can isolate mini-database state away from user app-data folders.
