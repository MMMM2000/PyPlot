2026-07-15 (UTC)

- Queue TMA per-run and developer run-log mirror writes on a serialized background writer so slow synced-drive files cannot freeze the UI or control loop, while keeping failures persistent and disabling only the failed target.
