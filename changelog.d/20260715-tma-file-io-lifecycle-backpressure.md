2026-07-15 (UTC)

- Bound TMA run-log backlogs, prefer per-run logs over optional mirrors under load, and report overload once without flooding the operator log.
- Persist explicit run-log completeness metadata with exact lost-line counts when a saturated, failed, or blocked session log cannot finish its bounded close flush, and finalize it only after the last authoritative session message.
- Isolate repeated sessions that reuse the same run-log path so a delayed write failure from an older session cannot disable or purge the replacement session's log.
- Run TMA and Current Annealing filesystem workers as window-free daemon tasks so permanently blocked calls cannot retain a closed window or abort process teardown, and limit TMA summary plotting to one active plus the latest pending run.
