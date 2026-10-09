# Transition detector benchmark

## Purpose and safety boundary

`scripts/benchmark_transition_detectors.py` compares the current Current Annealing and TMA automatic transition estimates with explicit reviewed decisions saved in a disposable `.pydpj` copy. It accepts only exact measurement paths inside explicit `--root` directories. It never writes measurement files, project files, or transition sidecars; CSV and JSON diagnostics are written only under `--out`.

Excluded targets are counted but omitted from detector-quality metrics. Reviewed `no_transition` targets are negative examples and therefore contribute to the false-positive rate.

## Prague benchmark

The 2026-08-03 benchmark used the SHA-256-verified disposable copy under `artifacts/transition-detector-benchmark-2026-07-30/`. Its approved roots were the Prague Current Annealing and TMA directories. The benchmark scored 169 Current Annealing targets and 164 TMA stress/sweep targets. Twenty-two Current Annealing records outside the approved Prague roots were blocked and not read.

| Metric | Current Annealing before | Current Annealing after | TMA before | TMA after |
| --- | ---: | ---: | ---: | ---: |
| Reviewed-label detection | 35.3% | 51.2% | 86.6% | 86.6% |
| Positive-target detection | 70.2% | 75.0% | 91.2% | 91.2% |
| No-transition false positives | 19.3% | 19.3% | 75.4% | 9.8% |
| Median absolute error | 1.89 mA | 1.43 mA | 1.15 mA | 1.15 mA |
| Mean absolute error | 3.62 mA | 3.03 mA | 3.01 mA | 3.01 mA |

Current Annealing now evaluates cooling independently of heating, while rejecting a clear wrong-signed heating transition. Its cooling search covers the reviewed low-current region more completely. TMA now rejects tangent fits unless the trace has at least 0.5% total strain span and the fitted transition contributes at least 0.15% strain excursion.

The remaining false positives are heterogeneous reviewed exceptions rather than a clean low-signal cluster: 11 Current Annealing targets and 6 TMA targets. Further global tightening would risk removing reviewed positive transitions, so those cases remain for manual review.

## Reproduce

```powershell
.\.venv\Scripts\python.exe scripts\benchmark_transition_detectors.py `
  --project artifacts\transition-detector-benchmark-2026-07-30\microwire_database_latest.copy.pydpj `
  --root "G:\My Drive\1 Projects\Praha\current annealing data" `
  --root "G:\My Drive\1 Projects\Praha\mini DMA" `
  --out artifacts\transition-detector-benchmark-2026-07-30\final-detectors
```

The reproducible outputs are `transition_detector_benchmark.json` and `transition_detector_comparisons.csv`.
