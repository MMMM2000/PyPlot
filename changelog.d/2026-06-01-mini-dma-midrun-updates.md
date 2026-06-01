2026-06-01 11:35

- Mini DMA current-sweep recipes can now apply visible current-sweep edits to remaining, not-yet-started sweeps while leaving the active sweep frozen and logging the runtime override.
- Runtime updates can re-plan future iso-load, iso-stress, or iso-strain target plateaus when target start/end/step changes are made mid-run.
- Current-sweep fields that cannot safely modify the active recipe are shown in a gray read-only state during a run, while runtime-editable fields remain normal.
