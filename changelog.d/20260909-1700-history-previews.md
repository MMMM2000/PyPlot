## Measurement history previews

- Added a resizable preview pane in Measurement History with current/time, resistance/time, and resistance/current graphs. Selecting a row previews it without replacing the main view; Open/double-click retains the existing full-load action.
- CSV preview loading runs in the background with selection debounce and stale-result rejection. Preview memory is bounded; sampled large-run overviews are labelled because brief events may be omitted. Errors appear in the preview pane without changing measurement files.
