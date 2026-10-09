### Fixed

- Keep Tic command-timeout resets and motor-status sampling on the hardware dispatcher thread, so UI delays cannot stall target acceptance or motor keepalive.
- Cancel and release queued or active motion state after Halt and Pause, and retry incomplete stationary moves from the confirmed Tic position instead of waiting forever or treating a one-unit miss as success.
- Verify that recipe preflight is connected to a Tic T500 as well as applying the shared 1/8-step motor profile.
- Require a brief stable endpoint dwell before finishing stress ramps, probe descending uncalibrated ramps in the correct direction, batch control-trace flushes, and refuse to create a second control worker while the first is still alive.
