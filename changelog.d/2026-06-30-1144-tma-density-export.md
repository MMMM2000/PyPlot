2026-06-30 11:44

- Added TMA transition current-density columns to the public Builder analysis export, derived from each row's measured core diameter and preserving explicit no-transition/not-observed statuses.
- Recalculate TMA peak strain from raw run folders during public export so stale saved project summaries cannot override the per-stress local-minimum baseline calculation.
- Removed noisy video-end, video-range, notes, and current-annealing-current columns from the public analysis sheet.
