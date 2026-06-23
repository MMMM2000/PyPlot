2026-06-23 14:05

- Added a software-only Mini DMA full-run simulator for first-overheating style target acquisition, current rise, endpoint recovery, reverse unwind, bounded mechanical corrections, delayed scale feedback, and slack take-up.
- Added full-run scenario reports and a parameter sweep that emit machine-readable summaries plus plots under `artifacts/`.
- Bounded processed-noise admission so very broad raw stress envelopes cannot hide a processed center that is materially off target.
