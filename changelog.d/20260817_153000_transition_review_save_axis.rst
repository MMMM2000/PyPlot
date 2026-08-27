Fixed
-----

- Keep the Microwire Data Builder responsive while large measurement payloads are encoded and written during project saves, and avoid reopening every TMA source run while reconciling transition reviews.
- Exclude TMA iso-current and iso-strain measurements from the actual transition-review queue instead of reporting them as load failures, without filtering current-annealing runs.
- Keep TMA strain in ordinary percent values across repeated target switches by permanently disabling SI-prefix ranges on the percent axis.
- Cache lightweight TMA reviewability checks so selecting table rows does not repeatedly rescan and downsample every stored TMA trace.
- Prepare TMA transition-review runs once in a background worker so opening the next sample keeps navigation and progress feedback responsive.
- Distinguish transition markers by branch colour and start/finish line style, stagger nearby labels, emphasize the selected point, and show exact values with the nearest curve point on hover.
