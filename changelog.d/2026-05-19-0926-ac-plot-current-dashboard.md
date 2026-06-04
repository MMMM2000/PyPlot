2026-05-19 09:26

- Improved the AC susceptibility live dashboard with measured-current plotting, optional wire-resistance and PSU-power channels, and an optional far-right Y axis.
- Switched the default AC plot layout to four AC-specific tiles: elapsed time, measured current, frequency, and amplitude.
- Made `Rs` and `Ls` plot as scatter-only by default, while wire resistance uses line plus symbols.
- Added display-space horizontal spreading for repeated current, frequency, and amplitude points so dense scans do not collapse into vertical stripes.
- Added optional UI timer telemetry to AC diagnostics so plot/UI responsiveness can be reviewed separately from acquisition logging.
