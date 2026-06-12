2026-06-12 08:33

- Moved Mini DMA recipe control ticks off the Qt UI timer so long measurements keep controlling even when the dashboard is visually busy.
- Batched live run-log updates and coalesced worker-triggered progress/label refreshes to reduce UI stutter during active Mini DMA runs.
- Kept live average-speed sampling independent from recipe timing so the speed display can update without disturbing control-loop timing.
