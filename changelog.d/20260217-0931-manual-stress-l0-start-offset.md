2026-02-17 09:31 UTC

- Manual Stress/Strain logger: when start displacement is set to 10 points, strain now uses an effective gauge length `L0_effective = L0_input - 0.1 mm` (instead of raw `L0_input`).
- Applied the same effective-`L0` logic to the dual-axis overlay top axis so strain ticks/labels stay consistent with the logged strain values.
- Changing start displacement mode now re-runs derived calculations for existing points to keep strain values in sync.
- Manual Stress/Strain logger: added `Show annealing graphs` using the connected `.pydpj` project, loading annealing source files from the project row and previewing separate **high-current** and **low-current** `Resistance vs Current` graphs.
- Manual Stress/Strain logger UI: compacted the left panel without scrolling by placing related controls side by side (name-builder `Reset` next to preset selector, `Auto-fill diameter` in the diameter row, project action buttons inline), keeping fields like `Notes` visible.
- Manual Stress/Strain logger: improved `Area` label formatting so small cross-sections no longer round to `0` (uses scientific notation for very small values) and displays the unit as `mm²`.
- Manual Stress/Strain logger: logged-data table now includes an extra `Micrometer points` column (derived from displacement), while file export format remains unchanged.
- Manual Stress/Strain logger: the table micrometer column now reflects the wrapped device display (`0..45`, step `5`) anchored to `Micrometer at d=...` and start mode, instead of raw unwrapped point counts.
