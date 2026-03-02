2026-03-02 07:20 UTC

- Fixed shared Project Explorer worksheet activation to open worksheet entries by key (not only path-backed items), and worksheet-group nodes now open their first worksheet when available.
- Hardened shared workbook/worksheet cleanup and Project Explorer focus sync against stale/deleted tree items to prevent runtime errors after closing/removing workbooks or switching tabs.
- Updated tight-layout warning handling so saved plugin graph-option overrides are auto-applied instead of repeatedly prompting for the same plugin.
- VSM Temperature Scan now appends filename-derived orientation tokens (for example `a000`, `a090`) to sample labels when header metadata does not include angle, keeping 0°/90° runs distinct.
- VSM Hysteresis metadata normalization now snaps near-integer temperatures (for example `-29.6`) to integer setpoints (for example `-30`) to avoid duplicate temperature groups/titles.
- Updated visual-check helper to snapshot and restore PyPlot QSettings so temporary visual validation runs do not overwrite the user’s saved import/export directory history.
- Fixed VSM Hysteresis plugin initialization to keep plugin-local settings separate from shared PyPlot settings, so global Graph options remain shared across plugins and persist across sessions.
