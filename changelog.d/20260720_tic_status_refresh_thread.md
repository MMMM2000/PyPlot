### Fixed

- Schedule Tic target-acceptance status refreshes on the Qt UI thread so recipe motor commands cannot starve readback and retry indefinitely against stale motor state.
