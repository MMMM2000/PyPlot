2026-10-22 13:10 UTC

- Enlarge Origin graph frames and run native smart legend positioning after setting the final axes and layout. Keep hidden legends hidden and preserve explicit export-placement overrides.
- Preserve gaps, scatter-only rendering, common marker shapes, marker sizes, line widths/dashes, view limits, logarithmic scales, annotations, regular histograms, and independent panels in shared Origin graph exports. Export 3D scatter as native XYZ with piece colors and an editable piece legend.
- Refresh graph data and styles when exporting. The FMR, PDF, Maxion, stress/temperature sensitivity and VSM temperature scan tools export their displayed graph tabs through the shared exporter; their existing data-only fallback remains available before plotting.
- Retain the added graph metadata in saved projects, wrap long Matplotlib captions, and give Strain 3D a readable default figure width.
