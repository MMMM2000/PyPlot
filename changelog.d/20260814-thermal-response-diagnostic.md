2026-08-14 09:33 UTC

- Add a controller-owned stationary thermal-response check for elastocaloric specimens. When reopening from a de-energized state it first ramps CH4 to the hold current without moving the motor; once prepared, it cycles CH4 below and back to that current and verifies the restored energized state before offering another jump.
- Log a baseline-selected fixed MLX90640 region of interest in `ir_temperature.csv` so small temperature responses can be averaged without following a wandering hottest pixel.
- Distinguish a retained, hardware-connected prepared series from a completed recipe whose hardware ownership was released.
- Keep the diagnostic control available after hardware auto-connect and after a retained preparation, clear the one-shot preparation state before subsequent measurements, and allow the strictly stationary diagnostic to run without force feedback while ordinary prepared jumps still require a fresh scale reading.
- Preserve an already-connected scale across the UI-to-controller ownership handoff even for stationary diagnostics that do not require force feedback for control, and stream their recorded current/temperature points back to the live dashboard when no scale-timestamp plot sample is available.
