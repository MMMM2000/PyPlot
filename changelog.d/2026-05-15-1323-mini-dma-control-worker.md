2026-05-15 13:23

- Move Mini DMA recipe/control ticks onto a worker scheduler with frozen run-start settings so Qt repaint lag and Matplotlib redraws do not pace hardware control or CSV/control-trace logging.
- Serialize Mini DMA PSU serial access between worker current commands and UI readbacks, correctly parse scientific-notation current replies, add a current-sweep channel selector, and reset the current channel to output off at `1 V` / `1 mA` whenever automation stops.
- Tighten the Mini DMA dashboard header so the current task uses a fixed single-line row, remove the redundant scale-rate cell, lighten live-plot markers/lines, keep older downsampled plot points visually stable, and remember current-sweep target ranges separately for iso-load, iso-stress, and iso-strain modes.
- Add UI telemetry documentation for event-loop heartbeat, live-label cadence, and dashboard graph redraw timing.
