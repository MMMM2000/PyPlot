2026-04-16 16:07 UTC

- Added a new `Mini DMA Logger` launcher app for early hardware-driven stress/strain work with a serial scale and Pololu Tic-controlled stepper stage.
- The logger now supports scale polling, Tic status and jog commands, software tare and position zeroing, displacement-controlled ramp/cycle/hold recipes, richer session metadata, and TXT/CSV/JSON session export.
- Added G&G scale diagnostics: a probe action, automatic no-data warnings, and UI guidance that G&G RS232 balances need a DB9 null modem crossover rather than a straight-through serial link.
- Reworked the `Mini DMA Logger` UI into a dashboard layout with hardware/specimen/recipe tabs, status cards, naming helpers, safety limits, and a cleaner plot/log split.
- TXT output now follows the existing manual stress/strain column convention so the saved files can be opened directly in the Shape Memory Stress/Strain plotting workflow.
