2026-02-12 10:41

- Fixed `VSM Temperature Scan` dual-axis PyPlot legends so combined `10000 Oe` + `50 Oe` plots list series from both left and right axes.
- Updated VSM Temperature Scan Origin export axis-title handling to use named Origin axes (`x`, `y`, `x2`) with robust fallbacks, so exported axis labels now match PyPlot labels.
- Added explicit Origin graph-title application for all VSM Temperature Scan exports (main, smoothed, derivative, and smoothed derivative) so each exported graph shows its full title consistently.
