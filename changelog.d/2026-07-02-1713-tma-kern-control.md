2026-07-02 17:13

- Made TMA current-hold control scale-profile aware: KERN KCP feedback now uses a 0.01 g quantization floor for post-move/worsened-response decisions while Prague G&G keeps its 0.005 g floor.
- Added conservative fast-feedback KERN hold caps (`0.08%` base, `0.092%` adaptive ceiling) so the higher sample cadence does not compound quantized feedback into unstable motor corrections.
- Added a Kosice KERN full-run simulator profile using today's mounted-wire geometry and included it in control-validation artifacts.
