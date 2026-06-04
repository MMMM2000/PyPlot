2026-05-20 13:36

- Moved the AC Susceptibility Logger live dashboard from Matplotlib redraws to
  persistent PyQtGraph plot items so graph refreshes stay lightweight during
  long runs.
- Reduced live-display point density for old parameter-scan data while leaving
  TSV logging complete and incremental.
- Documented the PyQtGraph dashboard behavior and updated AC diagnostics notes
  for displayed-point counts.
