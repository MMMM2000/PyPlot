2026-06-01 09:05

- Made the guarded shared-HMP live smoke aware of an intentionally powered Mini DMA CH3 motor rail so it can preserve that state while still requiring CH1 and CH4 to be safe.
- Record a short CH4 settling series in the live-smoke artifact so transient low-current readbacks do not look like steady-state Mini DMA current-control failures.
