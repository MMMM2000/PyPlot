2026-05-26 13:37

- Mini DMA length setup now asks for the mounted wire length once at the beginning, then computes unloaded `l0` from the return-to-zero motion.
- If setup starts above the configured preload, Mini DMA skips the preload ramp and settle instead of asking for a second length entry.
