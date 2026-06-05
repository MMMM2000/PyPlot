2026-06-05 00:58

- Added an experimental Mini DMA current-sweep bootstrap seek mode that caps unknown-stiffness stress/load corrections to a small motor-step-limited move and lets the pre-current target ramp learn live stiffness before freezing it for the actual current sweep.
- Added a startup scale-stability gate so load/stress corrections wait through transient balance readings after reconnect instead of reacting to bogus stress spikes.
