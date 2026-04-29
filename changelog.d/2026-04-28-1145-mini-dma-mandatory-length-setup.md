2026-04-28 11:45 UTC

- Made Mini DMA recipe startup run the length setup as a mandatory unlogged preparation step before normal recipe CSV/graph logging begins.
- Streamlined the specimen panel into a `Sample` tab by removing manual gauge-length, preload-zero, Tic-zero, manual session, and optional naming controls; sample naming now always updates from the naming fields.
- Added recipe-side stress/load equivalents, including ramp-rate equivalents, using the current wire diameter, and made those equivalent labels readable in the dark UI.
- Remembered sample naming fields and the last `.pydpj` project, auto-imported matching Builder diameter data on restore/name changes, and marked manual/unimported diameter values in red while still allowing manual edits.
- Added a live length-setup popup graph for load, stress, and displacement, plus a setup-specific stage speed so setup preload ramps are no longer capped by the calibration micro-move speed.
- Added a Developer-menu run-log file mirror for debugging and changed return-to-start after recipe completion to run as an unlogged recovery popup instead of normal recipe rows.
