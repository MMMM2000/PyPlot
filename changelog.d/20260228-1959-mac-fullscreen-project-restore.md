2026-02-28 19:59 UTC
- Fixed macOS fullscreen graph switching in the shared MDI tab proxy so switching between graphs keeps a single fullscreen subwindow instead of dropping into stacked/cascaded small windows.
- Fullscreen graph geometry now fills the available MDI viewport instead of aspect-fitting to a reduced letterboxed window.
- Current Annealing project persistence now saves and restores loaded data sources plus open/active plot tabs in `.pypj` files.
- Project load now auto-loads data for `auto_load_on_import` plugins when paths/workbooks are present but plugin runtime data has not been restored yet, preventing disabled Plot actions after reopen.
- Added a shared plugin project-state wrapper in PyPlot host save/load flow so all plugins persist/restore common source-selection state consistently, including plugins that also keep custom project state.
- Shared project restore now tracks whether plugin plots were open and regenerates graphs when needed, so plugins without custom tab serialization still reopen with plots available.
- VSM Hysteresis Loops now uses shared PyPlot project persistence/versioning instead of legacy overrides, restoring `.pypj` compatibility with shared host save/load.
