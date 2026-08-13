2026-08-13 13:00 UTC

- Route elastocaloric current-transition stress holds through the same processed-feedback, stiffness, correction-cap, and recovery policy used by normal iso-stress sweeps instead of limiting them to the generic 0.01 mm distribution nudge.
- Keep the post-transition elastocaloric baseline and pull/release phases targetless so the motor remains stationary during baseline recording and commanded mechanical jumps.
- Allow the momentary current-hold bypass in every running TMA recipe that reaches the shared pause-for-target-recovery controller, including iso-stress, iso-current stress ramps, and elastocaloric transitions.
- Require measured endpoint current plus fresh post-endpoint scale feedback before accepting current-transition stress recovery.
- Keep the elastocaloric transition settle in force control until the stress target remains recovered, then freeze the motor for the pre-pull baseline.
- Preserve the confirmed elastocaloric CH4 close policy across the dedicated-controller process boundary.
- Keep a successfully released elastocaloric specimen prepared in the dedicated controller so each later **Run next jump** creates a fresh run containing only a stationary thermal baseline, one jump, and one release.
- Add recipe-scoped elastocaloric acceleration (default 200000 Tic units) with feasibility reporting and automatic restoration of the normal 100000-unit idle profile after each run.
- Preserve CH4 through both automation teardown and session-file finalization, and publish the prepared state only after a fresh CH4 output-state/current readback confirms it is really on.
- Treat a rejected **Run next jump** precondition as a non-mutating command rejection instead of a controller fault, so it cannot trigger the emergency path that turns off CH3.
- Recognize prepared-series pull/release step names as real elastocaloric motion legs, preventing the post-baseline jump from collapsing to a zero-step move and aborting the retained current.
- Clear the previous terminal dashboard immediately and show the fresh-baseline state as soon as **Run next jump** is requested.
- Ignore queued snapshots and events from the previous prepared-series run so the dashboard follows and plots the newly requested jump instead of immediately returning to the old completed display.
