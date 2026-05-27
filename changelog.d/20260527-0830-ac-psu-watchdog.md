2026-05-27 08:30

- Added a detached AC susceptibility PSU watchdog so an app update, crash, or parent-process exit can still zero current, zero voltage, and turn output off.
- Active AC sweeps now refuse ordinary window-close requests until the sweep worker can stop and run its normal PSU shutdown path.
