### Fixed

- Enforce and read back one canonical 1/8-step Tic T500 motor profile in TMA preflight, preventing a controller's stored full-step mode from silently changing the application to 100 Tic units/mm.
- Record the verified persistent and runtime Tic profile, device serial, and profile fingerprint in each TMA run's metadata.
