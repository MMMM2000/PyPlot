2026-05-28 10:25

- Polished the Mini DMA recipe UI by hiding recipe save/load controls behind a Settings toggle, moving uncommon setup/manual-action controls into collapsed detail panels, and adding restore-defaults buttons for setup, current-sweep advanced caps, and manual actions.
- Changed current-sweep first overheating from repeating the first normal target to running one configurable fixed-stress preheat sweep before the normal target sequence.
- Separated first-overheating, target, and current-sweep controls into compact recipe sections; first-overheating shows the load equivalent beside its stress target, and return-to-start is implicit instead of a visible checkbox.
- Displayed Mini DMA sample diameters in micrometers in operator-facing recipe/project labels.
