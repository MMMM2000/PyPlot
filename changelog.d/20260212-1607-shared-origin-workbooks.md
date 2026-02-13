2026-02-12 16:07

- Added shared PyPlot plot-workbook generation for plugins that rely on the base workflow: plotted line data now auto-registers as `Plot data` worksheets/workbooks (XY column pairs) so workbook tooling is available even without plugin-specific workbook code.
- Added a shared `Open in Origin...` fallback for base plugins: the action now exports the active plugin's shared plot workbooks to Origin, fixing disabled/no-op Origin export behavior in plugins such as Manual Shape Memory Stress/Strain.
- Added a shared `_clear_tab_list(...)` tab-removal helper in PyPlot so plugin tab clearing uses the same internal teardown path, keeping plot/workbook state synchronized when regenerating graphs.
- Marked selected plugins with custom workbook pipelines to opt out of shared auto-workbook generation, preventing duplicate workbook entries where plugin-specific workbook registration already exists.
