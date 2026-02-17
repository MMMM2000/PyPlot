2026-02-17 09:31 UTC

- Manual Stress/Strain logger: when start displacement is set to 10 points, strain now uses an effective gauge length `L0_effective = L0_input - 0.1 mm` (instead of raw `L0_input`).
- Applied the same effective-`L0` logic to the dual-axis overlay top axis so strain ticks/labels stay consistent with the logged strain values.
- Changing start displacement mode now re-runs derived calculations for existing points to keep strain values in sync.
- Manual Stress/Strain logger: added `Show annealing graphs` using the connected `.pydpj` project, loading annealing source files from the project row and previewing separate **high-current** and **low-current** `Resistance vs Current` graphs.
