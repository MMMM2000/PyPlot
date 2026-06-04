2026-05-18 12:17

- Separated AC susceptibility current-supply settings from Current Annealing
  Logger supply settings so HMP/OWON choices no longer leak between the tools.
- Switched AC susceptibility live plots to small scatter markers and added
  display-only per-condition thinning for dense frequency/amplitude plots.
- Fixed time-based AC progress completion text and kept slow-LCR retry fallback
  measuring the full requested point duration after bounded retries are
  exhausted.
