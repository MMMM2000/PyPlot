2026-07-27

- Added portable, versioned transition-review sidecars with path-independent
  measurement fingerprints for Current Annealing and TMA runs.
- Current Annealing now writes new measurements as self-contained run folders;
  legacy flat-file imports remain supported.
- Both loggers offer transition-current review only after safe run finalization,
  and Builder preserves conflicting project and sidecar decisions.
- Added a dry-run-first retrospective backfill command.
