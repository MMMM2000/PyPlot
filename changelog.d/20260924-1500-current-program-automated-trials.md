### Added
- Explicit finite trials can continue from a resistance-target timeout into cooling and turn output off when cooling completes; existing interactive sequence behavior stays the default.
- A bounded Keithley batch runner previews 2–10 mA trials by default, checks for competing logger processes, owns channel A with an exclusive VISA lock and output-off preflight, and saves a separate CSV/metadata run at each current. Faults, incomplete cooling, or an unconfirmed shutdown stop the batch before another trial.
