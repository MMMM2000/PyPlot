2026-07-27 (UTC)

- Keep previous-run checks, first-overheating decisions, hardware preflight,
  continuity preparation, and mounted-length entry in the visible TMA UI.
- Transfer ownership only after those steps: stop UI acquisition, close PSU and
  Tic handles, release the Tic process lease, then let the child reacquire and
  verify hardware before starting the authoritative recipe and run log.
- Abort cleanly before spawning when preflight, length entry, or Tic quiescence
  fails, and hide the engineering-only process label during normal operation.
