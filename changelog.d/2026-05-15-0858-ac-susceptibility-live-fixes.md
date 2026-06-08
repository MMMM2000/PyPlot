2026-05-15 08:58

- Improved AC susceptibility Stop handling so settle waits process UI events and microwire sweep waits shut down safely when stopped.
- Made empty-coil baseline files flush each row while measuring, leaving usable partial TSV files after Stop or interruption.
- Let auto-detect reuse an already connected shared PSU selection instead of probing the same open COM port again.
- Updated LCR monitor-off command normalization and documented LCR comparator/status values seen during live empty-coil checks.
