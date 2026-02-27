2026-02-27 06:44 UTC
- Fixed VSM Hysteresis Loops tab registration and legend refresh paths to apply shared PyPlot graph options (grid, fonts, legend settings) instead of bypassing them.
- Updated VSM plot theme refresh to keep shared `show_grid` and shared font-size defaults intact when legends/theme are rebuilt.
- Fixed shared dark-graph theme toggling to preserve each graph's original grid visibility state instead of forcing grids on.
- Added shared tight-layout warning handling in PyPlot: when Matplotlib cannot apply tight layout, PyPlot now reports the likely oversized text object with the exact font size and logs the full size summary.
