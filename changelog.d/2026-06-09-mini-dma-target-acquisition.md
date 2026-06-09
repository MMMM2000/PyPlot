2026-06-09 09:17
- Fixed Mini DMA sample headers so stale auto-generated sample names from a previous wire are replaced when the Sample tab composition/wire fields identify a new sample.
- Smoothed Mini DMA current-sweep target acquisition by honoring the configured target ramp rate even when the stress/load error is still large, instead of pulsing at the stage speed cap before the current sweep starts.
