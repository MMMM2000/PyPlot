2026-03-14 00:05
- Added a shared `Create Figure...` workflow for arranging existing graph tabs into multi-panel publication figures with configurable rows/columns, shared X/Y scales, panel labels, and paper-size presets.
- Persisted layout-figure tabs in `.pypj` projects so multi-panel figures reopen with their copied data, legends, and annotation objects intact.
- Added regression coverage for layout-figure creation and project round-trip restore alongside the existing graph builder and annotation tests.
