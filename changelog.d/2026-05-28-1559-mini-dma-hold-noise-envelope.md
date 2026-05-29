2026-05-28 15:59

- Prevented Mini DMA current-hold recovery from treating one-sided transformation scatter as target recovery; the noise band now only accepts/restarts the current ramp when the recent load/stress window overlaps the target.
- Kept current-sweep target acceptance tied to the requested/noise tolerance instead of the motor-step physical floor, so short or stiff wires no longer treat very large stress errors as "reached" while the ramp keeps heating.
- Added a Mini DMA control-trace replay diagnostic script for identifying current-sweep accept decisions that were only accepted because of the motor-step physical floor.
- Added automatic control-trace replay diagnostics to unattended Mini DMA bench summaries after each saved run.
- Added 30 MPa current-ramp speed-ladder recipes, a guarded bench-plan example, and a ramp-speed comparison script for choosing the precision/time tradeoff from saved run folders.
