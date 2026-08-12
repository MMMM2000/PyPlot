### Fixed

- Treat the post-austenitization TMA position as the elastocaloric strain baseline, so a run pulls by the configured tensile jump and releases to that same captured position instead of seeking the specimen's earlier absolute zero-strain coordinate.
- Stop setup-preload target chasing after the first endpoint crossing and hold the motor during the settle period for both Prague and Košice scale policies.
- Keep run-summary generation alive when Windows or DriveFS transiently denies an advisory status-marker replacement.
