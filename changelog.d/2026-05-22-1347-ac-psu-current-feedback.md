2026-05-22 13:47

- Added measured-current feedback for AC susceptibility current sweeps so the PSU voltage limit is adjusted automatically from readback instead of used as one fixed compliance value.
- Low or unreachable OWON current points now log warnings and continue with the measured current; missing actual-current readback before a point still fails safely.
- Documented that `current_actual_a`, PSU voltage, resistance, and power are the source of truth for later AC susceptibility analysis.
