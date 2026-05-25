2026-05-20 09:56

- Mini DMA setup preload now derives the active ramp rate from the live starting load/stress to the requested preload target, so relaxing from a high preload uses the configured setup duration instead of the nominal zero-to-target ramp.
- Mini DMA length-setup progress now reports the active setup phase and phase percent instead of unstable global recipe tick counts.
- Mini DMA setup stable-time holds now reset when the preload or zero-load target is not actually reached, so the measured-length prompt waits for a continuous stable target during current-sweep setup.
- Mini DMA current-sweep load/stress correction now uses a robust recent scale signal for servo decisions, ignores single-sample balance spikes inside the noise band, and waits for a confirmed filtered reversal before sending the first opposite correction.
- Mini DMA current-hold resume now uses a separate automatic recovery tolerance band, so the current ramp continues once filtered stress is practically recovered instead of chasing final-tolerance fluctuations.
- Mini DMA setup slack take-up now exposes a configurable stiffness-prior step cap and defaults it to `50 MPa`, making pre-contact slack removal much faster while keeping feedback-gated moves bounded.
- Mini DMA mandatory length setup now refreshes the frozen control config after accepted starting length and computed `l0`, so strain logging and subsequent control use the measured setup length instead of a stale recipe-start value.
- Mini DMA setup return-to-zero now applies a small strain-based speed floor for tiny residual loads, avoiding very slow one-step unloads near baseline.
- Mini DMA length-setup plotting now snapshots setup samples before drawing, preventing live plot refresh crashes from concurrent sample updates.
- Mini DMA paused-current recovery can now use a local hold-only response stiffness after several confirmed correction samples, allowing faster load/stress recovery during transformations while keeping the frozen current-sweep stiffness and displacement/strain safety rails intact.
