2026-07-15 (UTC)

- Bound TMA run-log backlogs, prefer per-run logs over optional mirrors under load, and report overload once without flooding the operator log.
- Keep TMA and Current Annealing filesystem workers safely owned through cancellation and bounded window close, and limit TMA summary plotting to one active plus the latest pending run.
