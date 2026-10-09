### Changed

- Run full Windows verification with four isolated pytest-xdist workers by
  default, while keeping project package and safe-codec lock tests in a serial
  follow-up lane. Focused checks remain serial unless workers are requested.
