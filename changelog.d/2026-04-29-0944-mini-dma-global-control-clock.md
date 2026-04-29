2026-04-29 09:44

- Split Mini DMA recipe scheduling into global control, data-log, and UI-refresh intervals instead of per-recipe frequencies.
- Updated displacement, hold, calibration, Hsw, and current-sweep recipes to use timed steps and the global log cadence while keeping hardware polling/readback timers separate.
- Moved global timing controls into `Settings -> Timing...` and let target-ramp seeking advance planned motion between scale updates for smoother setup preload/current target ramps.
- Defaulted G&G request-mode scale acquisition to a 250 ms interval with a longer read timeout so the measured roughly 5 Hz balance response is treated as the hardware limit instead of a fast timeout.
