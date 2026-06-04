2026-05-29 17:32
- Added headless Microwire Data Builder automation coverage for updating the Current annealing section and rebuilding Assemble from the copied `.pydpj`.
- Current annealing section automation now skips parsed numeric files that do not contain a recognizable composition/draw/piece identity, so manifests report them as skipped instead of counting them as updated records that later disappear.
