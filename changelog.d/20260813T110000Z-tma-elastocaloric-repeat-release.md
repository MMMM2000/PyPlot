2026-08-13 11:00 UTC

- Made elastocaloric pull/release moves target the captured mechanical baseline absolutely and require confirmed Tic position readback before completion, preventing a pull-only run from being reported as successful.
- Added repeated elastocaloric measurements from one prepared austenitic baseline, preserving CH4 after a verified normal return while retaining fail-safe output shutdown for stops, faults, emergencies, and active-window closure.
- Added a guarded development-only option to preserve the confirmed elastocaloric baseline across a normal idle app close.
- Reflowed the elastocaloric recipe controls and replaced the native Windows cloud-folder picker to avoid cropped controls and DriveFS folder-selection freezes.
