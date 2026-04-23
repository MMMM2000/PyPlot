2026-04-23 07:33
- Current Annealing now gives repeated increasing/decreasing cycles their own color shades and per-cycle legend labels in both Matplotlib and the standalone Origin export path.
- Removed the redundant Current Annealing plugin-specific `Origin export` settings section so PyPlot uses the shared top-toolbar Origin actions for this workflow.
- Current Annealing import now distinguishes amp-vs-milliamp source data more safely and rejects files that would exceed the expected 1000 mA annealing ceiling after unit detection.
