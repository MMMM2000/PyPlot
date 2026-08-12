### Fixed

- Keep the TMA Hardware tab available as a live, read-only controller monitor during dedicated-process recipes, including bounded thermal-camera previews, while interlocking all hardware-changing UI controls.
- Stop elastocaloric recipes if their mandatory MLX90640 stream becomes stale, and record precise pull/release command, response, and recording-window markers in the authoritative control trace.
