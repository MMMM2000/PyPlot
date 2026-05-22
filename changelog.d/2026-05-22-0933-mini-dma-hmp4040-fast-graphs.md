2026-05-22 09:33

- Mini DMA adds HMP4040 support with auto-detect, 115200 baud defaults, current-sweep CH4, and motor-supply CH3 while keeping channels user-configurable.
- Mini DMA dashboard graphs now default to a 500 ms refresh interval and cache older downsampled history so long runs avoid rescanning the full run on each redraw.
- Mini DMA pyqtgraph tiles now keep the run log compact, leave right-edge breathing room, use less dense/thinner major gridlines, and color Y axes to match their plotted curves.
- Mini DMA pyqtgraph tiles now keep empty top/right axes visible as plain frame lines without tick marks or labels when no data axis is assigned there.
- Mini DMA manual setup now shows a modal progress dialog while Auto-connect hardware probes the motor, scale, and optional motor-supply channel.
- Mini DMA manual Auto-connect hardware now prepares the current-sweep supply channel with the configured voltage limit and starting current while keeping that channel output off, so HMP4040 CH4 does not retain stale front-panel settings.
- Mini DMA dashboard plot widgets now shrink correctly in the available panel height and keep the run log shorter so the lower-right graph stays inside the visible window.
