2026-07-02 22:59

- Made KERN fast-feedback current-hold drift recovery use bounded noise bands so high transition scatter no longer forces unstable single-step motor corrections while the filtered load/stress is moving farther from target.
- Calibrated the Košice KERN full-run simulator profile to the observed approximately 16 Hz effective raw scale sample rate from the live 2026-07-02 run.
- Made TMA control-trace replay output writing robust to deep Windows run-folder paths.
