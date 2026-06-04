2026-06-04 00:00
- Added opt-in AC Susceptibility Logger support for the shared HMP broker, including channel leases, broker readback, and channel-only shutdown/release on stop or error.
- Added a bounded continuous LCR debug JSONL sidecar for transition/cadence diagnosis, with persisted cadence and row-cap settings.
- Kept AC HMP4030/HMP4040 direct and shared-broker current sweeps at the configured voltage limit while changing only current setpoints, matching the other HMP logger integrations.
- Documented shared-broker operation, debug-stream metadata, and the live bench plan for MED/SLOW/AVG tuning.
