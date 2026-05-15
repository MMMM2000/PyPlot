2026-05-15 11:37

- Split Mini DMA live label/telemetry cadence from dashboard graph redraw cadence, defaulting dashboard Matplotlib refresh to 1000 ms while keeping live samples and hardware acquisition independent.
- Added Mini DMA UI heartbeat and graph-refresh fields to `ui_telemetry.csv` so event-loop responsiveness can be inspected separately from plot redraw timing.
