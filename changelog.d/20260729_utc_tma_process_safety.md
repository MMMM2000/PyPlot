- Begin the TMA terminology migration while retaining compatibility aliases for
  historical `mini_dma` imports and persisted project data.
- Make `data_logging.tma_logger` and the `tma` launcher identity canonical while
  preserving historical import, CLI, bench-role, and project-data aliases.
- Host TMA's auto-started shared HMP broker and serial driver in a dedicated OS
  process instead of the visible Qt process, with readiness reporting,
  parent-loss fail-safe handling, and bounded shutdown support.
- Transfer shared-HMP leases to the recipe child without release, configure,
  or output commands; use a unique session owner so a second TMA instance
  cannot adopt the first instance's channels.
- Add bounded command acknowledgement deadlines, queue-backpressure handling,
  forced child cleanup, and an independent broker all-output emergency path.
- Preserve the motor supply after normal recipe completion or operator Stop,
  while fault, crash, timeout, application close, and Emergency Stop retain
  all-output fail-safe behavior.
- Restore live child log delivery, full-range downsampled setup/run plots,
  pre-run dialog ordering, and optional IR acquisition after ownership transfer.
- Add spawned fake-hardware coverage for both Prague and Košice policies,
  pause/resume/completion logging, broker parent loss, startup failure, policy
  mismatch, command saturation, and UI acknowledgement timeout.
