2026-06-03 09:27

- Improved Mini DMA imports so selected run folders, parent folders containing multiple run folders, and multiple folder selections load without requiring a separate placeholder import file.
- Changed Mini DMA power top axes to default to length-normalized `Power/cm [mW/cm]` when initial-length metadata is available, with absolute `Power [mW]` still available from the Mini DMA plot settings.
- Marked Mini DMA first-overheating preheat sweeps as separate dashed diamond traces with compact `1st:` legend labels when run metadata identifies the first-overheating target.
