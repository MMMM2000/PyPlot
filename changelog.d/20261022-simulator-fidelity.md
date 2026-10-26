## 2026-10-22 UTC

- Add explicit controller stiffness assumptions and opt-in measurement delay,
  quantization and transformation-lag sensitivities to the offline iso-stress
  simulator, with versioned source/configuration provenance.
- Exercise missing feedback through the actual pure force-control policy;
  report true-error and dropout metrics separately.
- Add a read-only copied-trace audit with disjoint training/held-out comparisons,
  recorded-input rollout residuals, timing summaries and input hashes. These
  diagnostics do not establish hardware model validity or change production control.
