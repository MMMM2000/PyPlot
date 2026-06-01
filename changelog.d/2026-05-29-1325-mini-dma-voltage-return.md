2026-05-29 13:25

- Mini DMA current sweeps now keep the nominal reverse-current leg rate-limited when the supply remains near the voltage limit, avoiding an abrupt current drop or premature plateau transition.
- Mini DMA current-hold recovery now bypasses the persistence wait when filtered stress/load is moving rapidly away from the target, so transformation runaways get an immediate mechanical correction.
- Mini DMA current-hold recovery now keeps predictive multi-step corrections during rapidly moving-away transformations instead of throttling to motor-step corrections just because the previous feedback worsened.
- Mini DMA current-hold recovery now treats large off-target stress/load errors as actionable even when the filtered balance window is noisy.
- Mini DMA current-sweep recipe files now round-trip the disabled "return to start target" setting instead of forcing it back on during save/load.
- Mini DMA settings persistence no longer silently re-enables "return to start target" while closing or saving app settings.
- Mini DMA session metadata now preserves an earlier fault stop reason when the app closes afterward.
- Mini DMA control trace rows can record row-local task text so diagnostic traces do not inherit stale current-sweep task labels.
