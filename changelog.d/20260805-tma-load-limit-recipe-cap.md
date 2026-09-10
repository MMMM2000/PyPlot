### Fixed

- TMA current-sweep recipes now disclose and record when an applied-load safety limit clips the requested stress/load target sequence, require operator confirmation before starting, and stop at the last safe configured plateau instead of repeatedly requesting an unreachable target.
- Process-isolated TMA stop recovery now exposes the hardware-release transition and fences the recovery prompt and command so one operator choice opens one recovery view and starts one recovery action.
- Process-isolated TMA runs now render summary artifacts in the surviving visible process after the controller releases hardware, and HMP broker startup preserves the real child error while tolerating the brief Windows COM-handle handoff after detection.
