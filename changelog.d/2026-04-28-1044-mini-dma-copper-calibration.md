2026-04-28 10:44 UTC

- Added an automatic Mini DMA Calibration recipe that records baseline scale noise, preload targets, forward/reverse micro-move phases, and a JSON calibration report with stiffness, backlash, and stress-strain estimates when geometry is available.
- Split calibration preload seeking from micro-move characterization so bent/stiff calibration wires can be straightened with faster, coarser corrections before fine stiffness/backlash measurements.
- Shared the zero-load/length setup workflow with the Calibration recipe and kept old `calibration_copper` saved settings compatible with the new generic recipe name.
- Made the recipe panel size itself to the visible recipe page so calibration controls no longer leave a large blank area before the start button.
