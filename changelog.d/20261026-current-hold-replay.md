### Added
- Offline replay of the production scale filter and current-ramp hold methods,
  using isolated clocks and fake supply/motor boundaries without connecting hardware.
- Recorded-sidecar clock audits, causal UTC sensitivity scenarios, sample-index
  alignment where available, provenance reports and explicit replay limitations.
- Tests for real filter/hold behavior, delayed/stale inputs, motor-gated cycle
  resume, runtime overrides and no-future-input alignment. Live policy is unchanged.
