### Fixed

- TMA analysis exports keep excluded run targets out of cached strain rows and
  prevent their aggregate values from being assigned to other runs. Other targets
  and runs of the same wire remain available.
- CA, TMA and VSM analysis exports withhold pending, conflicted, superseded or
  disabled reviews, including cached fallbacks. Reviewed no-transition decisions
  remain visible.
