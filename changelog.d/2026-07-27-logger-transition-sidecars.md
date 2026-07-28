2026-07-27

- Added portable, versioned transition-review sidecars with path-independent
  measurement fingerprints for Current Annealing and TMA runs.
- Current Annealing now writes new measurements as self-contained run folders;
  legacy flat-file imports remain supported.
- Both loggers offer transition-current review only after safe run finalization,
  and Builder preserves conflicting project and sidecar decisions.
- Added a dry-run-first retrospective backfill command.
- Excluded CA/TMA targets retain their reviewed transition values for audit while
  remaining unavailable to Builder analysis; No transition remains value-free
  numerically but is retained as a useful categorical analysis result.
- Scoped the first retrospective transition-review campaign to Prague data only.
- The Current Annealing PyPlot plugin now accepts a run folder directly, uses
  the folder name for plots/workbooks, and can review older loaded runs into
  portable sidecars.
