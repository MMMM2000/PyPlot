2026-07-27 UTC

- Restored responsive UI-owned TMA manual motor jogging with bounded
  target-position steps. Releasing a jog button now stops scheduling movement
  immediately instead of waiting for a queued velocity halt, while active
  recipes remain isolated in the dedicated control process.
- Routed the visible Emergency Stop through the control child's out-of-band
  safety event and retained a red pending state until the child confirms its
  emergency safe state. Manual and Hardware controls are now interlocked while
  the child owns recipe hardware.
- Documented source-based launches as the supported deployment path; executable
  packaging is no longer a release gate.
