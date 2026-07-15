2026-07-15 (UTC)

- Bound TMA run-log backlogs, prefer per-run logs over optional mirrors under load, and report overload once without flooding the operator log.
- Persist explicit run-log completeness metadata when a saturated or blocked session log cannot finish its bounded close flush.
- Run TMA and Current Annealing filesystem workers as window-free daemon tasks so permanently blocked calls cannot retain a closed window or abort process teardown, and limit TMA summary plotting to one active plus the latest pending run.
