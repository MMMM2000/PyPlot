2026-06-09 09:17
- Fixed Mini DMA sample headers so stale auto-generated sample names from a previous wire are replaced when the Sample tab composition/wire fields identify a new sample.
- Smoothed Mini DMA current-sweep target acquisition by switching to small probing corrections after a load/stress reversal, while preserving fast stage-speed moves for large monotonic target errors.
- Made held-current Mini DMA recovery start with smaller response-probing corrections until local wire stiffness is learned, improving robustness for stiffer or transformation-active wires.
- Added a live instability escape path that backs off current by one supply step when held-current recovery shows large sign-changing stress excursions.
