2026-07-03 13:51

- Made TMA IR firmware flashing auto-detect installed STM32CubeCLT versions, including CMake, Ninja, GNU Arm tools, and STM32CubeProgrammer, instead of requiring a hard-coded CubeCLT version path.
- Added MLX90640 Cube firmware I2C diagnostics, electrical SCL/SDA drive self-tests, selectable H753ZI I2C pinsets, and recoverable camera-detection retries so wiring/power failures are visible instead of leaving the firmware in an error loop.
- Surfaced MLX90640 I2C scan failures in the TMA run log with actionable power, ground, and SDA/SCL checks.
