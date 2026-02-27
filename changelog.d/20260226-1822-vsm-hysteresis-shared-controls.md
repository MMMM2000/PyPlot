2026-02-26 18:22 UTC
- Removed the VSM Hysteresis Loops plug-in "Appearance" settings section so it no longer duplicates shared PyPlot graph controls.
- Switched VSM Hysteresis Loops to shared PyPlot workbook/Origin export flow, including shared `Open in Origin...` action routing.
- Updated VSM hysteresis settings/theme handling to remain compatible when legacy style/dark widget controls are absent.
- Updated VSM default loop axes to prefer varying `Applied Field For Plot [Oe]` / `Signal X direction [emu]` columns and kept automatic plot-time fallback to varying axes when selections are flat.
- Fixed VSM metadata parsing so explicit `Set Sample Temperature ...` entries are not skipped by earlier fallback tokens, preventing stray one-off temperature groups (for example `26 °C` outliers).
- Improved shared graph dark-theme restoration so legends reliably return to light styling when `Dark graphs` is turned off, even if legends were created while dark mode was active.
- Added shared Graph formatting legend orientation controls (`Auto`, `Vertical`, `Horizontal`) and wired them into both per-graph formatting applies and saved graph-option defaults.
- Added a shared activation-time subwindow normalization pass for the single-visible-graph case to avoid occasional narrow graph windows after app switching.
- Kept VSM bound methods from overriding shared PyPlot graph/object-manager handlers, so graph names, object manager behavior, and shared graph-format interactions remain consistent.
