2026-10-22 13:10 UTC

- Enlarge Origin graph frames and run native smart legend positioning after setting the final axes and layout. Keep hidden legends hidden and preserve explicit export-placement overrides.
- Preserve gaps, scatter-only rendering, common marker shapes, marker sizes, line widths/dashes, view limits, logarithmic scales, annotations, regular histograms, and independent panels in shared Origin graph exports. Export 3D scatter as native XYZ with piece colors and an editable piece legend.
- Refresh graph data and styles when exporting. FMR, PDF, Maxion and VSM temperature scan export their displayed graph tabs through the shared exporter. Sensitivity tools retain categorical captions, proxy legends and point colors through their dedicated exporters, with the enlarged frame and smart legend placement.
- Retain the added graph metadata in saved projects, wrap long Matplotlib captions, and give Strain 3D a readable default figure width.
- Preserve saved XY/XYZ roles, edited legend captions, split-trace styles without duplicate data, coincident 3D point names, and right axes within multi-panel figures. Stabilize Windows logger tests by collecting graphics wrappers after Qt teardown.
