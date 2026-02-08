2026-02-08 16:07 UTC

- Added headless GUI smoke tests using `pytest-qt` for launcher/workbench startup and blank-graph creation paths.
- Added deterministic parser fixtures under `tests/fixtures/` for DMA Iso-Stress and VSM temperature scan inputs with expected outputs.
- Updated test configuration to default Qt to offscreen mode in automated/headless runs (`PYTEST_GUI_HEADLESS=0` disables this).
- Dependency update: added `pytest-qt==4.5.0` to the `test` optional dependency set in `pyproject.toml`.
