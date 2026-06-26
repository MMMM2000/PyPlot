2026-06-19 08:25

- Replaced current-annealing manual transition-current storage with graph-level transition review records so values reviewed on one graph no longer appear on other graphs from the same microwire.
- Added fast current-annealing transition review queue actions for accepting, excluding, marking no-transition, and moving to the next unreviewed graph.
- Derived current-density summary values from accepted/included transition review records and added per-label current-density columns for every reviewed transition current.
