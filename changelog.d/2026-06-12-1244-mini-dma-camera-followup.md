2026-06-12 12:44
- Made the Mini DMA thermal-camera popup behave as a normal minimizable window on Windows, with a narrower layout and a 1 fps display option.
- Added IR camera/thermometer connection to Mini DMA manual hardware auto-connect when an IR port is selected.
- Simplified dashboard temperature plotting to a single Temperature (C) channel backed by coalesced IR values while preserving full-rate IR sidecar logging.
- Closed Mini DMA recipe sessions on control-stop recovery paths and snapshot-locked live buffers to prevent post-stop time-plot tails and deque mutation errors during refresh.
