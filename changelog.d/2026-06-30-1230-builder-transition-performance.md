2026-06-30 12:30

- Improved Microwire Data Builder responsiveness by preventing the TMA transition overview from reparsing every raw TMA run when refreshing status rows.
- Kept unloaded TMA runs visible in the transition overview while preserving saved reviewed/no-transition/excluded statuses.
- Preserved recalculated TMA strain summaries when applying reviewed TMA transition values, avoiding stale low strain text from saved project payloads.
- Reduced Assemble preview/export table overhead by adding missing columns in bulk instead of fragmenting the DataFrame one column at a time.
