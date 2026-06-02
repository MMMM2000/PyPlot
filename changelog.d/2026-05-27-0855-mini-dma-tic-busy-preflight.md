2026-05-27 08:55

- Mini DMA recipe preflight now reports Tic status read failures as a busy/unreadable controller instead of mislabeling unknown VIN as motor power off.
- Mini DMA unit tests now block accidental real Tic USB access unless a test installs an explicit fake backend.
