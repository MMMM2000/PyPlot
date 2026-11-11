## 2026-11-11 UTC

- Accept plain sN piece labels while preserving legacy labels, without inventing
  a default physical identity for new forms.
- Add explicit local piece selection to general, manual stress-strain, TMA,
  annealing, AC, current-program and microscope workflows.
- Preserve selected identities in supported run metadata; guard AC resume and
  prevent piece-specific microscope data leaking into wire-level Builder rows.
- Add explicit record exchange with atomic imports and visible collisions.
  This remains local/imported coverage, not a central cross-device allocator.
