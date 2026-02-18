2026-02-18 12:27 UTC
- Shared Origin export: replaced unsupported `PAGE.ANTIALIAS` and fragile axis/title assignment with Origin-compatible label commands (`label -s -n title`, `label -s -xb/-yl/-xt/-yr`) and per-layer antialias fallbacks.
- Shared Origin export: multi-axis worksheets are now grouped by axis metadata and plotted to separate linked layers (`layer -new Both`) so dual-axis overlays export with correct scales instead of an extra near-zero trace.
- PyPlot legends: dual-axis overlay legend rebuild now deduplicates labels across sibling axes and keeps a single host legend, preventing duplicate `Loading 1` entries.
- PyPlot MDI sizing: graph-option and graph-format applies now re-fit and re-arrange subwindows after size changes to avoid one graph appearing larger until focus switches.
