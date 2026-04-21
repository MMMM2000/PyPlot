2026-04-16 16:07 UTC

- Added a new `Mini DMA Logger` launcher app for early hardware-driven stress/strain work with a serial scale and Pololu Tic-controlled stepper stage.
- The logger now supports scale polling, Tic status and jog commands, software tare and position zeroing, displacement-controlled ramp/cycle/hold recipes, richer session metadata, and TXT/CSV/JSON session export.
- Added G&G scale diagnostics: a probe action, automatic no-data warnings, and UI guidance that G&G RS232 balances need a DB9 null modem crossover rather than a straight-through serial link.
- Reworked the `Mini DMA Logger` UI into a dashboard layout with hardware/specimen/recipe tabs, status cards, naming helpers, safety limits, and a cleaner plot/log split.
- Added integrated current-annealing control with reusable supply profiles, manual output control, live current/voltage/resistance/power logging, and mechanical-plus-heating recipe support in the same session.
- Added preload-aware strain zeroing with explicit `l0` gauge length handling so strain can stay pending until the sample is actually under load instead of during wire straightening.
- Added configurable four-tile plotting with dark-theme-aware Matplotlib styling, selectable channels per axis, DMA/heating/mechanical presets, and a dedicated popup plot editor so the live dashboard keeps more space for graphs.
- Added `.pydpj` specimen import so composition/sample naming and sample diameter can be pulled in from Microwire Data Builder projects for stress calculation.
- Added an initial `Hsw distribution` recipe mode that can step through load, stress, or strain plateaus with configurable tolerance, seek nudge, point count per plateau, and optional reverse sweep.
- Made the left-hand `Overview` section collapsible so the main working layout can prioritize controls and plots while still keeping the status cards available on demand.
- TXT output now follows the existing manual stress/strain column convention so the saved files can be opened directly in the Shape Memory Stress/Strain plotting workflow.
