2026-02-18 10:19 UTC
- Shape Memory Stress/Strain: dual-axis overlay now keeps a single segment legend (`Loading 1`, `Unloading 1`, ...) instead of separate `Load ...` and `Stress ...` legend groups.
- Shape Memory Stress/Strain: the selected graph layout mode (separate tabs vs dual-axis overlay) is now remembered across sessions.
- PyPlot Object Manager: double-clicking a legend now opens the shared `Graph formatting` legend controls so legend settings are consistent with the main formatting window.
- PyPlot MDI windows: hardened visibility-queue cleanup to avoid stale subwindow references that caused `wrapped C/C++ object ... has been deleted` runtime errors while switching/closing many graphs.
- Shared Origin export: per-series axis metadata is taken from the actual source axes (including multi-axis figures), and graph/axis title assignment now uses OriginPro API-first setters with LabTalk fallback to improve title reliability.
