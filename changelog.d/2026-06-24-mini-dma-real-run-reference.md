2026-06-24 01:35
- Added a software-only Mini DMA real-run reference summarizer that scans existing run-quality artifacts and measurement CSVs into calibration tables and plots for simulator scenario selection.
- Added a real-run-inspired thin 8.3 um high-strain/high-hold Mini DMA stress-ladder simulation case based on the Ni50Fe26Ga24 1/2 reference family.
- Added p95 stress-error metrics and automatic ranked policy-grid artifacts to the Mini DMA full-run simulator, so short transformation spikes stay visible without dominating sustained-error quality flags.
- Added a real-vs-simulation comparison tool that overlays real Mini DMA measurements with simulator outputs and reports strain/current/stress/hold similarity metrics.
- Added a `realistic_run32_first_target` Mini DMA simulator scenario calibrated to the real Ni50Fe27Ga23 12/2 run32 first 50 MPa target segment, including hidden free-strain roughness during transformation so stress fluctuations come from material state rather than fabricated measured strain.
