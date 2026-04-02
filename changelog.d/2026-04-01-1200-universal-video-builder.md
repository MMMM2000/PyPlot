2026-04-01 12:00 UTC
- Added a dedicated `Universal Video Builder` launcher entry for manual fabrication-video review outside the full Microwire Data Builder.
- Added a single-window fabrication/video workflow that scans connected fabrication roots, keeps fabrication data and linked videos in one table, and supports searchable composition selection plus multi-draw row adding.
- Refined the Universal Video Builder layout so the controls and guidance text stay compact and readable, while keeping missing-video rows red and using a softer review state for manual gaps.
- Made source-video launching more robust by falling back to the native OS file opener when Qt refuses to open a valid local video file.
- Added dedicated `.pydpj` save/load support for the new workflow under the `MicrowireVideoBuilder` project kind.
- Added project docs for the new manual-only workflow and documented that it does not use OCR.
- Fixed the Universal Video Builder so broad fabrication roots are scanned independently of annealing or microscope relevance filters from other builder sections, and ignore temporary `~$` Excel lock files during cataloging.
- Improved the Universal Video Builder add-microwire workflow with a scrollable multi-select draw picker that stays open while selecting several draws, a dedicated fabrication-spreadsheet open action, visible `d`/`D`/`d/D` fabrication columns, and filtering for empty placeholder tail pieces.
- Added `Remove selected row(s)` to the Universal Video Builder so mistakenly added microwire rows can be dropped from the current table without rescanning the fabrication root.
