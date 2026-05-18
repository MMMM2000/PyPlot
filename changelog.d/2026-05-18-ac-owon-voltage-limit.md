2026-05-18 15:58

- Clamp AC susceptibility OWON SPE6102 voltage setpoints to the bench-tested SCPI maximum of 61 V so the supply does not silently keep a zero-volt setpoint when a 62 V limit is requested.
- Migrate older saved OWON AC voltage limits of 5 V, 60 V, or 62 V to the safe 61 V default while preserving intentional lower user limits.
