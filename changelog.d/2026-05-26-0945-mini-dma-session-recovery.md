2026-05-26 09:45

- Added Mini DMA emergency session recovery when final metadata writes fail because the output folder was moved or temporarily unavailable.
- Removed the current-sweep "Settle after current" setting and post-sweep settle step; current recovery remains handled by the current-ramp hold controller.
