2026-07-02 17:29

- Calibrated the Kosice KERN TMA simulator profile to the observed 20 Hz / 50 ms scale feedback cadence, 0.01 g quantized readback, mounted-wire geometry, and controller wait overhead from the 2026-07-02 run.
- Added KERN-specific current-hold re-entry hysteresis so a just-resumed current ramp does not immediately pause again on a smaller same-sign rebound from the previous hold; the guard scales from the previous hold entry error instead of fixed MPa bands.
