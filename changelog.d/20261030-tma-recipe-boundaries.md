### Fixed

- TMA heating, cooling and residual strain use one contiguous recipe episode,
  excluding pre-load fragments and retaining recorded holds. The last commanded
  maximum defines the turnaround. Incomplete returns and ambiguous repeated
  cycles do not report full-cycle endpoint changes.
- Builder derives TMA metrics from its embedded measurement and frame-verified
  calibration, preserving saved target/load labels and reviewer exclusions.
- Analysis exports include the highest applied current setpoint actually reached
  as `TMA max current (mA)` separately from current at maximum strain.
- Saved accepted break observations export their applied current and nominal
  target stress, retaining event-only rows without accepting phase boundaries.
- Explicit owner resolutions of CA project/sidecar conflicts survive normal
  reopening and unchanged-sidecar refreshes; changed sidecars still need review.
