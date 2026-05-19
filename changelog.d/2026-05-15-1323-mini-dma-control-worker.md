2026-05-15 13:23

- Move Mini DMA recipe/control ticks onto a worker scheduler with frozen run-start settings so Qt repaint lag and Matplotlib redraws do not pace hardware control or CSV/control-trace logging.
- Serialize Mini DMA PSU serial access between worker current commands and UI readbacks, correctly parse scientific-notation current replies, add a current-sweep channel selector, and reset the current channel to output off at `1 V` / `1 mA` whenever automation stops.
- Add UI telemetry documentation for event-loop heartbeat, live-label cadence, and dashboard graph redraw timing.
