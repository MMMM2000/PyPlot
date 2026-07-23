### Fixed

- Keep Builder automation refresh payloads in isolated memory until the updated
  v3 project package is written, so large graph collections no longer fail at
  the standalone JSON size limit.
- Report the active section while a Builder automation refresh is running, so
  slow remote-folder reads are diagnosable instead of appearing frozen.
- Report each TMA run before it is parsed during automation, making a blocked
  cloud-backed measurement file identifiable without altering review state.
- Discover TMA `measurement.csv` candidates from directory metadata and defer
  file-content validation to the parser, avoiding a redundant cloud-file read
  that could stall before progress reporting began.
- Prune configured archive, cache, test, and temporary directories while
  walking measurement roots instead of traversing them before filtering.
- Allow automation recipes to bound source-folder traversal with `max_depth`;
  the Praha TMA refresh can scan its immediate run folders without entering
  ancillary per-run trees.
