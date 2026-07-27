2026-07-27 UTC

- Restored the PR 298 UI-owned continuous-velocity manual motor jog. Releasing
  a jog button now sends a priority halt that cancels any undispatched velocity
  command, leaving no position destination for the motor to finish after
  release. Active recipes remain isolated in the dedicated control process.
- Restored the visible existing-output review before the mounted-length prompt.
  The UI records the operator's save-next or replace choice, while the dedicated
  controller remains the only process that creates and writes the run files.
- Fenced the visible window's periodic Tic status timer during controller-child
  ownership so it cannot reacquire the motor immediately after handoff. Child
  startup faults now retain their original traceback in the process log.
- Routed the visible Emergency Stop through the control child's out-of-band
  safety event and retained a red pending state until the child confirms its
  emergency safe state. Manual and Hardware controls are now interlocked while
  the child owns recipe hardware.
- Documented source-based launches as the supported deployment path; executable
  packaging is no longer a release gate.
