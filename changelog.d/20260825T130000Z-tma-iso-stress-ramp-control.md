### Fixed

- Made setup and every iso-stress plateau transition share one stable moving-ramp controller, with 200 ms Prague-scale feedback, monotonic rate-sized corrections, robust multi-point stiffness learning, and a bounded setup identification retry when the unload fit is not trustworthy.
- Made the run-specific zero-load calibration use the observed stable slack plateau rather than requiring agreement with the stored scale tare, so scale drift does not bias the fitted zero-stress position.
