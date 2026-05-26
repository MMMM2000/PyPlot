2026-04-23 13:22

- Added a persistent PyPlot automation session mode in `launcher.py` with `--pyplot-session-start`, `--pyplot-session-state`, `--pyplot-session-send`, `--pyplot-session-close`, and `--pyplot-session-list`, so Codex can keep driving one live PyPlot window across multiple follow-up commands instead of reopening PyPlot for every batch job.
- Added a public `PyPlotWorkbench` automation API for plugin selection, imports, plotting, tab activation, shared exports, project save/load, figure-building, and screenshot capture; the batch automation path now uses the same API instead of private widget pokes.
- Added launcher coverage for the live-session control flow, including a cross-process test that starts a real session, imports data, generates a graph, captures a plot image, and closes the session cleanly.
