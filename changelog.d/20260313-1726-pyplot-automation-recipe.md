2026-03-13 17:26
- Added a canonical `launcher.py --automation-recipe <job.json>` entrypoint for PyPlot machine-facing automation, including recipe validation, hidden/offscreen execution, `.pypj` load/save support, and machine-readable manifest output.
- Added deterministic batch plot-image export support for automation runs so visible PyPlot tabs can be saved as numbered PNGs for replayable testing and agent workflows.
- Reserved recipe `kind: "builder"` for future `.pydpj` automation without implementing that mode yet.
