2026-03-02 09:50 UTC
- PyPlot `Check outliers...` now opens a visual preview dialog (tabbed per worksheet) showing the exact flagged rows and highlighted trigger columns before removal.
- VSM Temperature Scan plotting now preserves first-measured order for field/series plotting and legends instead of forcing high-field-first ordering.
- VSM Temperature Scan colors are now direction-aware: heating segments always use warm tones and cooling segments always use cold tones.
- Data Builder VSM Temperature Scan grouping now keeps the parser-provided sample label (including orientation/variants), so samples like `... no glass` and `... no glass 2` remain separate entries.
