2026-02-19 12:19 UTC
- Shared Origin export: replaced fragile axis-title LabTalk syntax with direct axis-title commands (`label -xb/-yl/-xt/-yr`) so top/right titles for dual-axis overlays apply reliably.
- Shared Origin export: switched graph-title creation to a named centered label (`label -p 50 2 -j 2 -n PYPLOTTITLE`) with explicit font sizing, preventing missing/misplaced titles on some Origin builds.
- Shared Origin export: removed explicit legend reconstruction LabTalk calls in shared export because some Origin builds emit repeated `LEGEND.SMARTPOS` errors despite plotting correctly.
- Shared Origin export: dual-axis secondary layer now uses `layadd type:=txry` plus explicit axis visibility (`x/y` off, `x2/y2` on) so duplicate bottom/left axes are not generated.
