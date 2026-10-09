2026-06-29 13:27

- Preserve current-annealing transition reviews when refreshed graph records receive new internal IDs by remapping stale review entries through their source graph path.
- Debounce current-annealing transition review saves so actions such as "No transition" advance without blocking on an immediate store write.
- Fix current-annealing plot and Origin labels to display the omega unit symbol correctly.
