2026-02-19 12:19 UTC
- Shared Origin export: replaced fragile axis-title LabTalk syntax with direct axis-title commands (`label -xb/-yl/-xt/-yr`) so top/right titles for dual-axis overlays apply reliably.
- Shared Origin export: graph title now uses an explicit centered page label (`label -p ... -j 1 -n title`) with large font sizing, avoiding scale-attached title drift.
- Shared Origin export: dual-axis secondary layer uses `layadd type:=txry`, with both `layer.*.showAxes/showLabels` and `axis -ps` compatibility commands to enforce bottom/left-only primary axes and top/right-only secondary axes.
- Shared Origin export: prefer built-in Origin templates (`line`, then `scatter`) before the default user template to reduce template-script side effects (including recurring legend smart-position errors on some setups).
- Shape Memory parser: drop leading zero-load rows until the first non-zero load point so pre-load baseline zeros are excluded from segmented plotting/export.
