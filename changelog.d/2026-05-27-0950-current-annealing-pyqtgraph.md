2026-05-27 09:50

- Migrated the Current Annealing live dashboard to pyqtgraph when available, with a Matplotlib fallback for environments without pyqtgraph.
- Batched live Current Annealing plot segments by sweep direction to keep long measurements responsive.
- Made the Current Annealing shared HMP broker profile the default, require an explicit channel selection, and populate valid channel choices from detected HMP4030/HMP4040 capabilities.
- Updated Current Annealing HMP defaults to 32.05 V and 0.2 mA resolution, renamed the step control to current ramp rate, and quantized unsupported ramp rates to the HMP current resolution.
- Added Current Annealing metadata sidecars under `metadata/<data-file-stem>/metadata.json` while keeping the normal `.txt` data file in the selected output folder.
- Removed live Current Annealing dashboard gridlines and added visible top/right plot borders.
- Reworked the Current Annealing hardware header so broker/direct serial controls are not cramped, kept channel selection available before connection, hid obsolete hold-current controls, and made stale broker ports fall back to the standard shared broker port `8765`.
- Let Current Annealing start its own shared HMP broker from the selected HMP COM port when no broker is already running, while preserving the explicitly selected channel after connection.
- Removed the shared HMP setup helper from the main launcher experiment list.
- Added Mini DMA hardware-detection guards that block nonpreferred HMP supply baud rates and non-bench-standard scale baud rates, with clear operator messages before recipe use.
- Expanded Current Annealing and Mini DMA metadata with detected hardware/backend details, including ports, baud preferences, shared-broker source, channel choices, and Tic native-USB recipe policy.
- Added software-only shared-broker coverage for running Current Annealing and Mini DMA leases on separate HMP4040 channels at the same time.
