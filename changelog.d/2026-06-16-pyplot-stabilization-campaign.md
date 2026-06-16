2026-06-16 21:35

- Reorganized the AC susceptibility logger setup panel into clearer output, hardware, LCR, measurement-plan, and run-status sections with passive output-path and shared-broker lease status text.
- Added clearer shared HMP broker diagnostics for unreachable brokers, refused leases, stale leases, direct-serial access denial, wrong channel/profile, and stale channel-limit failures.
- Made Mini DMA shared-broker control retry once after stale lease errors and report broker connection failures with operator-facing diagnostics.
- Added cached Mini DMA Builder project sample suggestions for faster sample naming/autofill refreshes during background project imports.
- Expanded Mini DMA run-quality and core-plot summaries with stop classification, metadata warnings, current-hold recovery windows, voltage/current compliance events, and richer plot annotations.
- Made Mini DMA trace replay tolerate missing/invalid metadata or trace files and report warnings instead of failing before analysis.
- Stopped Mini DMA current-sweep voltage-limit unwinds immediately when the supply indicates open circuit or wire contact loss, before any mechanical recovery seek is attempted.
- Scoped Mini DMA predictive seek control to active controlled phases while still honoring explicit calibrated or live stiffness for ordinary load/stress seeks.
- Made Mini DMA IR thread disconnect/close cleanup tolerate naturally finished Qt threads that have already been deleted.
- Made Mini DMA saved Builder project import cancellation clear pending retry state and tolerate already-deleted Qt thread wrappers.
- Cleaned up Current Annealing fabrication-folder background-load completion so the UI resets promptly when the worker has already finished.
- Added Mini DMA elastocaloric recipe JSON round-trip coverage and offscreen UI screenshot evidence for the fast strain-jump workflow.
