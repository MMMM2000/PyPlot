2026-02-13 12:20

- Fixed shared PyPlot `Export TXT...` behavior for plugins that rely on base actions: the toolbar action now enables from plotted tab data and export falls back to Matplotlib lines when plugin line-state metadata is absent.
- Fixed shared Origin workbook export/session handling so `Open in Origin...` and workbook export keep Origin open instead of immediately exiting the Origin session.
- Restored shared side dock switcher buttons (Project Explorer/Object Manager) across platforms.
- Upgraded shared Graph formatting UI into tabbed sections (`Text`, `Axes`, `Ticks`, `Legend`) and added legend location/font/columns/symbol/follow-color/draggable controls to the same shared window.
- Added `Settings -> Graph options...` with global defaults and optional plugin-specific overrides for shared graph/legend defaults.
- Updated the Shape Memory Stress/Strain plugin to use shared action-state wiring so shared toolbar actions (save/normalize/TXT/Origin) follow plugin/tab readiness.
- Fixed shared side-panel behavior: dock switchers now use click-toggle mode and no longer force Project Explorer/Object Manager visible, preventing resize flashing and allowing panels to stay hidden when toggled off.
- Upgraded shared `Open in Origin...` to create Origin graphs from exported worksheets (not only transfer worksheet data).
- Standardized shared Origin metadata mapping for worksheet headers: `Long Name` stores physical quantity, `Units` stores units, and `Comments` stores legend/series labels.
