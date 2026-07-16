# AGENT GUIDELINES

## Repository Safety

- Treat `main` as the stable runnable baseline. Make substantive changes on a focused branch/worktree.
- Never force-sync, reset, discard, overwrite, or remove user work without explicit authorization.
- For user-visible changes on a feature branch, add a UTC-dated fragment under `changelog.d/`. Consolidate fragments only when preparing a release or integration to `main`.

## Test Data and Artifacts

- Never test against the user's real `.pypj`, `.pydpj`, or persistent app-data stores. Use disposable copies, synthetic data, and isolated storage roots.
- Put temporary tests, screenshots, caches, and diagnostics under ignored `artifacts/` paths.

## Environment

- Use `uv` with the Python version required by `pyproject.toml`. When dependencies change, keep `pyproject.toml`, `uv.lock`, and compatibility requirement exports aligned.
- On this Windows machine, use workspace-local ASCII temp/cache paths under `artifacts/` when needed. For headless checks, use `QT_QPA_PLATFORM=offscreen` and `MPLBACKEND=Agg` where appropriate.

## Hardware Safety

- Hardware work requires explicit authorization for that task.
- Prefer fake drivers and software checks first. Before live work, confirm there are no duplicate controllers and establish process and channel ownership, limits, safety rails, and the expected final output state. After errors or cancellation, verify safe output state where possible.
- Live TMA optimization must use an approved `campaign.yaml` and pass `uv run python scripts/mini_dma_campaign_check.py <campaign.yaml>` before execution. Do not start from chat memory, stale branches, or isolated artifacts.

## Diagnostics

- For crashes and runtime errors, inspect `logs/message_log.txt` and relevant files under `logs/` first.
