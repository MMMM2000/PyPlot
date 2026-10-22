# AGENT GUIDELINES

## Repository Safety

- Treat `main` as the stable runnable baseline. Make substantive changes on a focused branch/worktree.
- Inspect branch, base/head, upstream and local changes first. The master owns scope and integration; workers return focused commits/PRs and exact verification evidence. Check overlapping ownership before editing shared files.
- Keep changing device/bench, branch, PR, owner and acceptance state in a task ledger/handoff, not this file. See `docs/work-register.md` for the handoff fields.
- Never force-sync, reset, discard, overwrite, or remove user work without explicit authorization.
- For user-visible changes on a feature branch, add a UTC-dated fragment under `changelog.d/`. Keep existing fragments during routine cleanup; consolidate only when preparing a release or integration to `main`.

## Test Data and Artifacts

- Never test against the user's real `.pypj`, `.pydpj`, or persistent app-data stores. Use disposable copies, synthetic data, and isolated storage roots.
- Do not add real research measurements, fabrication workbooks, videos or database copies to Git. Routine tests must run offline with small deterministic synthetic fixtures and explicit expected results. Connected Drive sources are for selected, hash-recorded disposable scientific validation copies, not CI fixtures. Preserve raw/shared sources.
- Put temporary tests, screenshots, caches, and diagnostics under ignored `artifacts/` paths. Ignored also contains research deliverables, source copies and recovery images: verify ownership, backups and retention before any cleanup; unknown ownership means preserve.

## Builder Database Corrections

- Never rewrite `.pydpj` ZIP members directly. Correct a disposable copy using validated Builder writers so saved bytes and manifest descriptors agree. Manifest recovery must be explicit and copy-only; never weaken ordinary import validation.
- Before an authorized shared-database publication, verify every package entry and isolated Builder opening plus logger imports. Preserve intentional scientific corrections and deferred payloads, an exact backup, expected old-target SHA-256 and publication receipt. Use a reviewed publication workflow actually available on the branch; preserve stale-target, save-lock and integrity checks.

## Environment

- Windows is the primary development and acceptance platform. Linux setup/portability is outside routine scope unless explicitly requested. Keep Origin/Office and live instrument acceptance with the authorized local owner.
- Use `uv` with the Python version required by `pyproject.toml`. When dependencies change, keep `pyproject.toml`, `uv.lock`, and compatibility requirement exports aligned.
- On this Windows machine, use workspace-local ASCII temp/cache paths under `artifacts/` when needed. For headless checks, use `QT_QPA_PLATFORM=offscreen` and `MPLBACKEND=Agg` where appropriate.
- Isolate Qt settings, Builder storage and plotting caches. Report the tested commit, commands, results, skips and remaining acceptance limits. Software/fake-driver checks do not establish live hardware or Origin/Office acceptance.

## Hardware Safety

- Hardware work requires explicit authorization for that task.
- Prefer fake drivers and software checks first. Before live work, confirm there are no duplicate controllers and establish process and channel ownership, limits, safety rails, and the expected final output state. After errors or cancellation, verify safe output state where possible.
- Live TMA optimization must use an approved `campaign.yaml` and pass `uv run python scripts/mini_dma_campaign_check.py <campaign.yaml>` before execution. Do not start from chat memory, stale branches, or isolated artifacts.

## Diagnostics

- For crashes and runtime errors, inspect `logs/message_log.txt` and relevant files under `logs/` first.
