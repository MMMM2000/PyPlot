### Fixed

- Let an iso-current transition finish after held-current stress recovery remains accepted by the backlash-aware mechanical seeker, instead of re-entering a stricter endpoint-recovery loop; the dashboard now describes held-current mechanical recovery and zero-span current holds accurately.

### Added

- Add optional first overheating to iso-current stress-strain recipes, reusing one complete iso-stress current loop (up to the configured maximum and back) before the constant-current mechanical scan, with top-positioned, consistently sized controls, stress-ramp load-rate equivalents, persisted settings, safety-limit accounting, metadata, and recipe-file support.
