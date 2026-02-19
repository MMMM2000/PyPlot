2026-02-19 12:19 UTC
- Shared Origin export: replaced fragile axis-title LabTalk syntax with direct axis-title commands (`label -xb/-yl/-xt/-yr`) so top/right titles for dual-axis overlays apply reliably.
- Shared Origin export: graph title now uses an explicit centered page label (`label -p ... -j 1 -n title`) with large font sizing, avoiding scale-attached title drift.
- Shared Origin export: dual-axis secondary layer now prefers `graph.add_layer(4)` (`TopXRightY`) and applies final axis visibility via direct layer properties (`x/y showAxes/showLabels`) after title assignment, preventing interleaved duplicate tick labels.
- Shared Origin export: removed `layadd` fallback and now relies on `graph.add_layer(4)` only for dual-axis layers, avoiding Origin template-side `LEGEND.SMARTPOS` expression errors in affected builds.
- Shared Origin export: avoids writing `layer.*.showLabels=2` on secondary layers (Origin 2026 can flip `x2/y2` label mode to duplicated labels); side visibility is now enforced via `x.showlabel/x2.showlabel` and `y.showlabel/y2.showlabel`.
- Shared Origin export: prefer built-in Origin templates (`line`, then `scatter`) before the default user template to reduce template-script side effects (including recurring legend smart-position errors on some setups).
- Shared Origin export: now tries explicit built-in Origin template paths first (including `<Origin EXE>\\ORIGIN.OTP`) and logs per-graph template/layer-axis snapshots into PyPlot Message Log for runtime diagnosis.
- Shared Origin export: removed `layer -aa 1` in dual-axis export; on Origin 2026 this command can force both-side axes/labels (`showAxes=3`), producing duplicated/interleaved tick labels.
- Shape Memory parser: drop leading zero-load rows until the first non-zero load point so pre-load baseline zeros are excluded from segmented plotting/export.
