2026-05-15 13:23

- Move Mini DMA recipe/control ticks onto a worker scheduler with frozen run-start settings so Qt repaint lag and Matplotlib redraws do not pace hardware control or CSV/control-trace logging.
- Add UI telemetry documentation for event-loop heartbeat, live-label cadence, and dashboard graph redraw timing.
