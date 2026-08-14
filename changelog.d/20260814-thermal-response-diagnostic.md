2026-08-14 09:33 UTC

- Add a controller-owned stationary thermal-response check for retained elastocaloric specimens. It keeps the motor fixed, cycles CH4 below and back to the prepared current, and verifies the restored energized state before offering another jump.
- Log a baseline-selected fixed MLX90640 region of interest in `ir_temperature.csv` so small temperature responses can be averaged without following a wandering hottest pixel.
- Distinguish a retained, hardware-connected prepared series from a completed recipe whose hardware ownership was released.
