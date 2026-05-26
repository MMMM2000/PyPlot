2026-05-21 07:04

- Mini DMA live dashboard, setup, and recovery graphs now use persistent pyqtgraph widgets instead of redrawing Matplotlib figures for each refresh.
- Mini DMA dashboard plots keep left/right channel axes while updating existing curve data, reducing redraw work during long logged runs.
