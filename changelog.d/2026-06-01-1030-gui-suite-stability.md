2026-06-01 10:30
- Made Mini DMA Logger startup width adjustments avoid fragile internal Qt child widgets, improving startup stability in long GUI sessions.
- Guarded PyPlot subwindow state handling against non-state-change Qt events that can arrive during window close in full-suite runs.
