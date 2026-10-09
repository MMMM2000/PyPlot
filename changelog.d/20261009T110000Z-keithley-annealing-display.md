### Fixed
- Keep the full finite Keithley annealing run in the live plots without lowering acquisition or saved-data rates; use peak-preserving rendering downsampling.
- Color high-rate ramps by their acquired commanded setpoints instead of applying HMP-sized thresholds to noisy microsteps.
- Remove stale plot-header layouts on rebuilding and wrap long measurement titles.

Endless recipes still retain a bounded recent history; raw measurements remain on disk.
