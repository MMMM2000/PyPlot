2026-06-05 00:58

- Added an experimental Mini DMA current-sweep bootstrap seek mode that caps unknown-stiffness stress/load corrections to a small motor-step-limited move and lets the pre-current target ramp learn live stiffness before freezing it for the actual current sweep.
- Added a startup scale-stability gate so load/stress corrections wait through transient balance readings after reconnect instead of reacting to bogus stress spikes.
- Added a low-current voltage-compliance guard so current-sweep runs stop at the 1 mA continuity point when CH4 is open or contact-limited.
- Added an optional shared-HMP current-path probe for unattended Mini DMA bench plans and campaign manifests so CH4 conduction is proven before a long current-sweep recipe starts.
- Changed recipe/fault stops to explicitly disable the Mini DMA motor supply output as part of cleanup.
