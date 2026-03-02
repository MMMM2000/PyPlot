2026-03-01 08:55 UTC
- Switched PyPlot plotting workflows to the shared status-bar progress API (removed modal per-plugin progress dialogs in Current Annealing, Stress Dependence, and Stress Sensitivity).
- Updated shared import progress to use the status-bar progress bar instead of a separate modal progress window.
- Reordered status-bar widgets so task progress appears to the right of the live `x/y` cursor indicator.
- Project Explorer now switches graphs on selection change, enabling quick Up/Down keyboard traversal between plot tabs.
- Project Explorer graph selection now preserves tree focus after tab activation so repeated Up/Down traversal keeps working.
- Optimized large Current Annealing plot batches by throttling progress/event updates and reducing repaint overhead while tabs are created.
- Added shared graph-canvas quick actions: `Cmd/Ctrl+C` copies the active graph as PNG to clipboard, and right-click shows `Copy graph as PNG` / `Export graph...`.
- Improved shared graph rescale robustness (including FMR): when Matplotlib autoscale does not update limits, PyPlot now falls back to visible line-data bounds.
