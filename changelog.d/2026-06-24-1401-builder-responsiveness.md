2026-06-24 14:01
- Improved Microwire Data Builder startup and project auto-open responsiveness by skipping stale section-store loads when a startup project will replace them.
- Kept auto-open project loads quiet and non-modal, reduced duplicate fabrication/video sync work after project load, and deferred visible VSM table thumbnail rendering.
