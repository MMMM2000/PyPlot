2026-05-22 12:21

- Added the shared HMP4030/HMP4040 power-supply broker foundation with channel leases, model-aware channel validation, serialized SCPI channel operations, guarded global commands, and a localhost JSON protocol.
- Added a Shared HMP PSU Setup utility for confirming channel wiring and saving reviewed bench profiles before shared-output control.
- Added an optional Shared HMP broker supply mode to Current Annealing Logger so it can lease and control only a confirmed current-annealing channel while preserving the existing direct serial mode.
- Documented the shared HMP broker safety model and the current HMP4040 bench-channel example.
