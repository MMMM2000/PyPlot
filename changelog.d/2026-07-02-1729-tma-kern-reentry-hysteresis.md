2026-07-02 17:29

- Calibrated the Kosice KERN TMA simulator profile to the observed 20 Hz / 50 ms scale feedback cadence, 0.01 g quantized readback, mounted-wire geometry, and controller wait overhead from the 2026-07-02 run.
- Live-tested a KERN current-hold re-entry hysteresis candidate and left it disabled after it worsened stress-error p95 on the 2026-07-02 Kosice run; the accepted controller remains the KERN earned-resume/latest-sample path without fixed MPa hold bands.
- Live-tested a noise-gated KERN latest-sample lag-clear variant and left the latest-sample bypass disabled after it ran slower with worse p95 than the earned-resume controller.
- Refreshed the Mini DMA shared-HMP verification helper so it runs the current broker auto-start and correction-travel tests instead of an obsolete correction-travel abort test.
- Added a KERN-only current-hold runaway-drift recovery path so fast KCP scale feedback can send bounded dynamic corrections when filtered stress/load is still moving away from target, without changing Prague-scale volatile-response waits.
