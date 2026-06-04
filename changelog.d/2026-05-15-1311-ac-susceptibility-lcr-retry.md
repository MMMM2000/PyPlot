2026-05-15 13:11

- Added automatic AC susceptibility LCR cadence recovery for overnight runs:
  high-frequency FAST settings now log a warning, reconfigure the LCR meter,
  discard a short recovery window, and retry instead of waiting for operator
  confirmation when valid readings arrive suspiciously slowly.
- Applied the same recovery behavior to empty-coil baselines and microwire
  current sweeps.
