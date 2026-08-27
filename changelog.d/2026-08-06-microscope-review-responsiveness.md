### Fixed

- Keep microscope Enter reviews responsive by coalescing downstream updates and deferring hidden current-density recalculation until that tab is shown.
- Preserve the previously displayed microscope image pair when a refresh finds repeat images for the same microwire.

### Added

- Add a compact `Missing d/D only` microscope filter and allow double-clicking an inline preview to open the original image at full resolution.
- Use the same portable PyQtGraph transition reviewer from Builder, TMA Logger, and Current Annealing Logger; Builder saves run sidecars and mirrors them into the project snapshot.