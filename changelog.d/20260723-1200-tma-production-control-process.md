2026-07-23 (UTC)

- Run Mini DMA/TMA recipe control in a dedicated spawned process so visible Qt
  stalls cannot delay scale acquisition, Tic/PSU confirmation, recipe timing,
  safety decisions, or authoritative measurement logging.
- Add bounded session-scoped IPC, heartbeat/crash emergency handling,
  downsampled immutable snapshots, and exclusive UI/child hardware ownership.
- Preserve the existing Prague and Košice controller policies and provide
  deterministic fake-process coverage without issuing live hardware commands.
