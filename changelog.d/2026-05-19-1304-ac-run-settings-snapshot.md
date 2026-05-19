2026-05-19 13:04

- Added self-describing AC run metadata snapshots to baseline and microwire TSV files, including LCR settings, acquisition timing, current-loop points, and PSU configuration.
- Added an optional 0 mA reference point before the microwire current loop for OWON setups that cannot regulate below about 10 mA.
- Kept AC PSU profile refresh from overwriting the saved OWON COM port with unrelated serial devices.
- Simplified AC dashboard identification with colored Y-axis labels, primary-axis-only grids, and no in-plot legends.
