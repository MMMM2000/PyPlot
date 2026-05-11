2026-05-11 10:46
- Added a Thermal Camera Viewer experiment that connects to the Nucleo MLX90640 text-frame stream, reconstructs the 32x24 heatmap live, displays frame statistics, and exports the current frame to Downloads.
- Added a fast 921600-baud binary MLX90640 stream firmware and binary parser path for higher live-view throughput while keeping the text frame-dump mode as a fallback.
