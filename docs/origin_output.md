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

