2026-07-02 17:29

- Calibrated the Kosice KERN TMA simulator profile to the observed 20 Hz / 50 ms scale feedback cadence, 0.01 g quantized readback, mounted-wire geometry, and controller wait overhead from the 2026-07-02 run.
- Live-tested a KERN current-hold re-entry hysteresis candidate and left it disabled after it worsened stress-error p95 on the 2026-07-02 Kosice run; the accepted controller remains the KERN earned-resume/latest-sample path without fixed MPa hold bands.
- Tightened the KERN latest-sample lag-clear path so one-count quantized jitter no longer clears post-move feedback waiting; latest samples must move the target error meaningfully relative to the quantization/noise floor.
