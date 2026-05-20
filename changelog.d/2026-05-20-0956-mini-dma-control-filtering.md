2026-05-20 09:56

- Mini DMA setup preload now derives the active ramp rate from the live starting load/stress to the requested preload target, so relaxing from a high preload uses the configured setup duration instead of the nominal zero-to-target ramp.
- Mini DMA length-setup progress now reports the active setup phase and phase percent instead of unstable global recipe tick counts.
- Mini DMA current-sweep load/stress correction now uses a robust recent scale signal for servo decisions, ignores single-sample balance spikes inside the noise band, and waits for a confirmed filtered reversal before sending the first opposite correction.
