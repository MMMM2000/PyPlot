2026-06-03 09:05

- Included TMA `current_hold` displacement rows in Builder auto-extracted strain and transition summaries so recovery data is not dropped between current-ramp points.
- Rejected tangent-intersection transition fits when an intercept lands far away from the measured segment boundary, avoiding TMA transition-current summaries for unsupported strain jumps.
- Added conservative current annealing transition-current summaries that require accepted tangent fits on both increasing-current and decreasing-current sweep legs before reporting As/Af/Ms/Mf values.
- Fed accepted current annealing transition candidates into the Current density section as first-pass As/Af/Ms/Mf values while keeping manual Current density edits authoritative.
