### Added

- Run automatic shared-PSU Current Annealing recipes in a dedicated, Qt-independent control process with bounded IPC, heartbeat supervision, broker lease ownership, safe stop/emergency handling, and broken-contact detection.
- Store each isolated Current Annealing run in a unique authoritative run folder with UTC timestamps, recipe and hardware metadata, heating/cooling direction, current density, measured power and energy, run logs, and phone/detail summary artifacts.

### Changed

- Keep the visible Current Annealing UI as a downsampled snapshot consumer during isolated automatic runs while preserving the existing direct-serial and manual workflows for compatibility.
- Keep the spawned Current Annealing controller import path headless so normal Windows/manual launches do not depend on launcher-added UI module paths.
- Launch the Current Annealing application with the console Python runtime (without showing a console window) so its dedicated controller can spawn reliably on Windows.
- Restore usable idle controls and show the child bootstrap diagnostic when an isolated Current Annealing controller cannot start.
- Let PyPlot and Microwire Data Builder discover and read authoritative Current Annealing v2 run folders while retaining legacy `.txt` and `.dat` imports.
