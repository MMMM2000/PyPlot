2026-06-05 15:45

- Added operator-guided LCR open/short fixture correction controls to the AC Susceptibility Logger.
- Recorded LCR correction state in AC baseline and sweep metadata so runs show whether open/short correction was enabled.
- Added an offline AC empty-coil subtraction tool that writes a derived TSV with baseline-subtracted LCR columns while preserving raw measured columns.
- Kept AC auto-connect from resetting selected LCR frequencies/amplitudes and added an in-app busy progress state while open/short correction runs on the meter.
