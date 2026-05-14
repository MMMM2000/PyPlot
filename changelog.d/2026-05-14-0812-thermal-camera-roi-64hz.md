2026-05-14 08:12

- Added centered ROI streaming for the STM32Cube MLX90640 raw protocol so narrow wire views can run cleanly at 64 Hz while preserving EEPROM-based Celsius conversion in the PyPlot viewer.
- Updated the Cube raw packet parser and capture tool to accept compact ROI packet widths inferred from packet word counts.
- Added STM32 I2C bus recovery before MLX90640 startup to recover from reset-mid-transaction bus stalls.
