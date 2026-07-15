2026-07-15 15:32 UTC

- Keep TMA scale, IR, serial-scan, run-summary, and control callbacks on ownership-safe paths, reject superseded worker results, and retain blocked sensor threads without retaining or destroying a closed window.
- Revalidate TMA sensor tokens inside state mutation locks, repair the first real scale sample's cached zero reference before conversion/logging, and stop all periodic callbacks when a TMA window closes.
- Detach TMA raw scale and IR log targets before session shutdown, then close them after any accepted in-flight write finishes without blocking the GUI.
- Stop and disconnect Current Annealing GUI timers and serial callbacks during close so late progress, delay, provenance, and experiment events cannot touch closed widgets.
