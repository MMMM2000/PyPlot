2026-03-01 20:00
- Shared graph canvas resizing now keeps the configured figure width/height as fixed base/export dimensions and scales display via DPI, so graph content (text/lines/markers) zooms proportionally instead of being compressed.
- Resizing behavior is applied from the shared PyPlot window layer, so all plugins using Matplotlib graph tabs inherit the same fixed-dimension + proportional-zoom behavior.
- Added regression tests for resize-driven display scaling with fixed figure inches and for preserving Graph formatting dimensions after subwindow resizes.
