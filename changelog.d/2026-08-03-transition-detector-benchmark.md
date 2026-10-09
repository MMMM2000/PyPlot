Changed
-------
- Benchmark Current Annealing and TMA transition detectors against reviewed decisions from an exact-path, read-only project copy.
- Retain clear Current Annealing cooling transitions even when heating is not detected, while preserving wrong-signed heating rejection.
- Reject low-amplitude TMA tangent fits that match trace noise rather than reviewed transformations.
- Add an exact-path-only historical backfill mode that skips recursive filename fallback.
