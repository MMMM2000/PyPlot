2026-05-23 14:31 UTC

- Added `uv.lock` and made uv the preferred environment sync path for PyPlot development and Codex worktrees.
- Kept `requirements.txt` and `requirements-win.txt` as pip compatibility exports for machines and packaging scripts that still need them.
- Pinned the PyQt Qt runtime packages explicitly so uv-created environments keep the tested PyQt6/Qt runtime pairing.
- Added a Microwire Data Builder storage-root override for tests so automated runs can isolate mini-database state away from user app-data folders.
- Tightened Windows Codex setup so it checks for Python 3.14 with the `py` launcher before running `uv sync`.
- Shortened Windows pytest temp paths when needed so deep Google Drive fixture paths do not exceed Windows path limits.
- Kept Mini DMA recipe-completion tests headless on Windows by stubbing recovery hardware preflight.
- Sanitized serial logger output filenames as well as subfolder names so Windows-invalid characters do not trigger blocking error dialogs.
- Waited for the Microwire EDA worker thread cleanup in its progress-dialog test to avoid Windows QThread teardown crashes.
- Ordered the Windows test collection so the Mini DMA logger tests run before Microwire Builder/EDA GUI tests, avoiding an order-dependent native Qt teardown crash.
