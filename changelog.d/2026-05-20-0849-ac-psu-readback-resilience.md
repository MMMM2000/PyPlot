2026-05-20 08:49
- Made AC microwire sweeps tolerate transient missing PSU actual-current readbacks after current has already been confirmed, logging WARN rows instead of aborting overnight runs.
- Kept the hard safe-shutdown path for non-zero current points where the PSU reports actual current far below the requested value, and documented the readback/wire-break behavior.
