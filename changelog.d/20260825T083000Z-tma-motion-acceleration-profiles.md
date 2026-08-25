### Changed

- TMA automated motion now defaults to `25 mm/s²` acceleration/deceleration while idle and manual motion retain a separate, configurable gentler profile. Recipe preflight and shutdown switch and verify the appropriate Tic profile without changing the existing per-move speed limits.
