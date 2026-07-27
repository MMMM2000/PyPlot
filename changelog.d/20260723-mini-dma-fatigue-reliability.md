Mini DMA/TMA iso-stress fatigue recipes now exclude operator-paused time from
active ramp clocks, begin measurement logging immediately when automated setup
is disabled, and can resume a manually stopped sweep in a new run from the
saved current setpoint.

Fatigue measurement and control-trace CSVs now identify the cycle and up/down
leg. The UI also states whether scale settings select Prague legacy or Košice
adaptive force control, including the Prague-only automated setup path.

Fatigue cycles are now scheduled incrementally instead of eagerly expanding the
whole run at startup. The editor supports very large finite counts and an
explicit Forever option that runs until the operator stops it or a safety
diagnostic fires. UI plotting remains downsampled independently while complete
measurement data continues to be written to the run files.

Forever fatigue runs retain only a bounded recent measurement history in RAM
while preserving the full measurement CSV and accurate total-point metadata.
Emergency recovery metadata explicitly identifies when its reconstructed
measurement copy contains only the retained recent window.

This does not add a cumulative motor-travel or specimen-strain limit; existing
unlimited correction travel remains unchanged.
