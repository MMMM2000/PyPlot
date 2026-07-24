2026-07-14 09:45

- Replaced Microwire Data Builder project payload and persistent-store pickle reads/writes with a versioned, allowlisted JSON codec while preserving reviewed CA/VSM/TMA states, explicit no-transition values, and saved Assemble column visibility/order.
- Open legacy v1 projects in degraded read-only mode and block ordinary Save/Save As until an explicitly trusted copy is migrated to a distinct v2 output with the new launcher migration command.
- Surface malformed v2 stores as blocked read-only sections instead of caching or overwriting them, with explicit race-safe quarantine/repair handling.
- Fixed Builder project-load rollback initialization so a failed staged load reports and recovers instead of raising during state capture or appearing frozen behind an error dialog.
