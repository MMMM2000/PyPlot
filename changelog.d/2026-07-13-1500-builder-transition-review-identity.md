2026-07-13 15:00

- Preserved reviewed VSM transitions and phase points while scans are filtered or hidden, and safely reconnected VSM and TMA review states after equivalent source files or run folders are moved or renamed.
- Migrated legacy path-based VSM and TMA review records when a unique source match exists while retaining ambiguous reviews without applying them to the wrong measurement.
- When a VSM or TMA source path is reused for different data, reconnect the old review immediately if its original content moved elsewhere; otherwise retain it unmatched instead of applying it to the replacement. Failed project loads also restore the prior VSM preview records cleanly.
