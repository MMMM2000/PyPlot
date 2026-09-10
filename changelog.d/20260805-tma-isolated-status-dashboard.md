2026-08-05 UTC

- Keep the TMA dashboard task, live hardware values, recipe progress, and completed fatigue-cycle state synchronized with the dedicated control process, without reviving overlapping legacy status labels.
- Pause TMA recipes and turn off the heating/current channel when motor-supply VIN remains low, refuse resume until motor power is confirmed, and audit every shared-HMP output command with channel, owner, and UTC timestamp.
