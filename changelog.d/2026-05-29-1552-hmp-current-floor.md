2026-05-29 15:52

- Treat HMP4030/HMP4040 positive current setpoints below `1 mA` as the measured `1 mA` supply floor, while keeping output-off shutdown as the real zero/safety state.
- Keep Mini DMA direct-HMP, Mini DMA shared-broker, Current Annealing direct-HMP, and the shared HMP broker aligned on the same HMP current normalization behavior.
