### Fixed

- Prevented the Košice KERN force controller from escalating motor corrections before the processed scale signal contains the preceding move response. Košice control now uses a short trend-aware estimator, waits through its post-move observation window, caps corrections relative to the active load target, and retries unobservable responses without geometric growth. The Prague scale retains its legacy control path.
