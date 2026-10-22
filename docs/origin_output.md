# Origin Output Expectations

This document defines the expected Origin workbook + graph behavior for PyPlot and Microwire Data Builder exports.

## Global Rules

- One workbook per graph.
- Use one worksheet per graph unless a plug-in explicitly requires extra sheets (for example, VSM temperature scan uses Data/Smoothed/Derivative).
- Fill Long Name, Units, and Comments rows for every worksheet.
- Comments rows must label each series (angles, stress levels, heating/cooling sections, sub-version labels).
- Use distinct line/marker colors for each series and ensure legend entries match those labels.
- Titles and axis labels must match the Matplotlib view; Y labels must not be cropped.
- Avoid invalid LabTalk assignments (no PAGE.ANTIALIAS errors).
- Release Origin automation control after export so Origin can be closed independently of PyPlot.

## Displayed graph export

After plotting in PyPlot, **Open in Origin** exports the displayed artists and
their current view. It refreshes cached graph tables first, so subsequent edits
to data, visibility, line styles or axis limits are included. This also applies
to FMR, PDF Plotter, Maxion Continuous and VSM Temperature Scan.
Their existing direct data export remains the fallback
when no graph tabs have been generated. Workbook-only exports retain their
separate data/smoothed/derivative sheets where supplied by a tool.

Stress Sensitivity and Temperature Sensitivity retain their dedicated exporters
to preserve categorical sample captions, proxy legends and alternating point
colors. These exporters use the enlarged frame and final smart legend placement.

Combined legends keep their entries from both axes, including edited captions.
Saved workbooks retain their explicit XY/XYZ column roles. Split heating/cooling
traces export each segment once with its own style. Right-axis title placement
uses the actual axis range and reserves space for tick labels; no absolute data
coordinate is used as a caption limit.

The shared exporter preserves NaN gaps, scatter-only traces, common marker
shapes and point sizes, line widths/dashes, linear/logarithmic scales, view
limits, and hidden legends. Regularly spaced linear ticks retain their step.
Independent subplots use independent Origin layers; coincident axes retain the
existing twin-axis export, including right axes within independent panels.
Upper-panel tick labels and empty captions remain
hidden. Data, axes-relative and figure-relative text captions remain editable.
Regular vertical histograms export their bin centers/counts as native columns
with matching gaps. Unequal-width or stacked bars are not supported.
Simple Matplotlib math spans are translated into Origin Greek letters and
subscript/superscript formatting.

The normal single-panel frame occupies 80% of page width and 72% of page height.
Top power axes and right axes reserve extra caption space. Once the final
geometry, scales and limits are applied, `legend.smartpos=1` requests Origin's
one-shot automatic placement. Explicit `origin_legend_position` overrides still
take precedence. The command is documented in Origin's
[graphic object reference](https://docs.originlab.com/labtalk/ref/graphic-objs/).
Automatic placement selects an available region; it cannot guarantee a completely
clear legend for every densely populated graph. The resulting legend is editable.

3D scatter exports use the original XYZ coordinates even after Matplotlib has
projected the display into 2D. Named or individually colored pieces become
separate native plots with an editable piece legend outside the cube. At most
256 separately named/colored groups are supported. The native Origin view and
camera differ from Matplotlib, and 3D point text is represented by the legend.
Custom marker paths, variable per-point symbol sizes, complex mathematical
typesetting and arbitrary non-text artists are outside this fidelity guarantee.
Unsupported axis scale types report an export error. Annotation coordinate
systems that cannot be represented are reported in the log.
Filled bands and error-bar collections are omitted with a log warning; they
are never interpreted as a scatter point at zero.

Native command failures use the underlying `LT_execute` status when available;
the SDK's `lt_exec` wrapper discards that status. Graph callback failures are
reported as graph failures instead of silently counting the graph as successful.

Regression coverage is in `tests/test_origin_plot_fidelity.py` and
`tests/test_shared_origin_helpers.py`. Native validation must use disposable
fixtures, isolated settings, and an exclusively owned Origin session; Python
boundary tests alone do not establish native rendering quality.

## VSM Hysteresis

- Workbook naming: sample + temperature (and sub-version label, if present).
- Worksheet columns are paired XY sets per angle:
  - X: Applied Field for plot (Oe)
  - Y: Signal X direction (emu)
- Units row: `Oe` for X, `emu` for Y.
- Comments row: angle labels for each pair (0, 10, 20, ... 180 deg).
- Plot overlays all angles on a single graph per temperature.

## VSM Temperature Scan

- Workbook naming: sample + field strength (and sub-version label, if present).
- Data is split into heating/cooling sections as needed.
- If multiple sheets exist (Data/Smoothed/Derivative), each sheet must include units + comments rows.
- Comments row must capture section labels (for example, "10000 Oe up", "10000 Oe down").

## DMA Iso-Stress

- Workbook naming: sample + sub-version label (s1/s3, etc.).
- Worksheet columns are paired XY sets per stress level:
  - X: Temperature (C)
  - Y: Strain (%)
- Units row: `C` for X, `%` for Y.
- Comments row: stress labels (65 MPa, 97 MPa, etc.).

## Current Annealing (when exporting Origin workbooks)

- Workbook naming matches the graph label used in PyPlot/Data Builder previews.
- Units/comments rows are filled for current and temperature axes where applicable.

