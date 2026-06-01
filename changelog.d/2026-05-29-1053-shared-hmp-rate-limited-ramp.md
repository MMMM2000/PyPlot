2026-05-29 10:53

- Added shared HMP broker rate-limited current ramps so scheduled current changes are quantized to the supply resolution and delayed scheduler ticks do not create larger catch-up jumps.
- Routed Current Annealing shared-broker setpoints and Mini DMA shared-broker recipe current sweeps through the new ramp scheduler when available.
- Added broker scheduler metrics and override behavior so direct setpoints and active ramps cannot fight each other on the same channel.
