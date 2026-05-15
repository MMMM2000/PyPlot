2026-05-15 11:33

- Moved AC susceptibility baseline and current-sweep acquisition into worker
  threads so Matplotlib redraws cannot slow instrument logging.
- Throttled AC live-plot redraws to a one-second dashboard cadence while still
  flushing every measurement row to disk immediately.
