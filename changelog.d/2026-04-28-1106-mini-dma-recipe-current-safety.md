2026-04-28 11:06 UTC

- Removed the Mini DMA hardware-tab separate heating program; current is now recipe-owned.
- Changed Mini DMA voltage-limit handling during current sweeps to ramp recipe current back to `0 mA` and continue instead of stopping the whole recipe.
- Made the zero-load hanging-weight reference the default max applied-load ceiling, with the custom max-load setting acting only as an optional lower limit.
