2026-07-22 (UTC)

- Prefer the HMP native USB-D/HO720 virtual COM interface during Current Annealing and TMA power-supply auto-detection while retaining RS232 adapters as fallbacks.
- Add visible 1 Hz and up-to-2 Hz PSU readback choices to both loggers.
- Add broker-owned bounded polling, cached immutable readbacks, coalesced current commands, lease-bound cadence generations, and fair 2 Hz sharing.
- Warn before a second run reduces an existing 2 Hz broker client to 1 Hz, show/log effective cadence transitions, and return the remaining client to 2 Hz after release.
- Preserve Current Annealing ramp rates in mA/s across cadence changes with resolution-aware averaged setpoints.
