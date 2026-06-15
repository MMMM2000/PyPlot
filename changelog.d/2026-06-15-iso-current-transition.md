2026-06-15 10:30

- Rename the Mini DMA constant-current stress-strain recipe UI to iso-current stress-strain.
- Add an iso-current current-transition ramp that holds a low stress target while ramping to each current level before the stress-strain scan.
- Rework the iso-current recipe page into target, mechanical scan, current level, and collapsible current-transition sections with current-density/load equivalents.
- Always scan iso-current legs up and back to the start target, and default fixed-step motion to 0.2 mm/s while still waiting for fresh scale feedback.
- Delay fixed-step stress-strain logging until fresh post-move feedback is available, reducing stale-feedback strain jumps.
