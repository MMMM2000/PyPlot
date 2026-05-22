2026-05-22 09:33

- Mini DMA adds HMP4040 support with auto-detect, 115200 baud defaults, current-sweep CH4, and motor-supply CH3 while keeping channels user-configurable.
- Mini DMA dashboard graphs now default to a 500 ms refresh interval and cache older downsampled history so long runs avoid rescanning the full run on each redraw.
- Mini DMA pyqtgraph tiles now keep the run log compact, leave right-edge breathing room, use less dense/thinner major gridlines, and color Y axes to match their plotted curves.
- Mini DMA pyqtgraph tiles now keep empty top/right axes visible as plain frame lines without tick marks or labels when no data axis is assigned there.
