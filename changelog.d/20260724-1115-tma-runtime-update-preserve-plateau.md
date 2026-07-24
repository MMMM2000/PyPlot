2026-07-24 (UTC)

- Preserve the current stress/load plateau's pending current sweeps when
  applying recipe edits during its target ramp or settling phase.
- Prevent a mid-run current-rate edit from advancing directly to the next
  plateau before the current plateau has completed.
