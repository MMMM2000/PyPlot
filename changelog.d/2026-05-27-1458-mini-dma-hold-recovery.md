2026-05-27 14:58

- Mini DMA paused-current recovery no longer stops only because held-current transformation corrections exceed the per-target no-response travel counter.
- Mini DMA paused-current recovery now uses the configured fast current-sweep stage-speed cap while stress/load is far outside the held-current recovery band, and avoids forcing those large-error transformations down to one motor step after a worsening feedback sample.
- Mini DMA paused-current recovery no longer waits a full filter window for an unchanged filtered signal while stress/load is still far outside the held-current recovery band.
- Mini DMA paused-current recovery now keeps the current ramp held after a single accepted recovery seek until either the filtered resume band or repeated accepted recovery seeks confirm stable recovery.
- Mini DMA current-hold entry now confirms transformation onset faster using an automatic tolerance-scaled sustained-error band, so current ramping pauses closer to the first target departure without a fixed MPa entry floor.
- Mini DMA large-error held-current recovery keeps the fast recovery trigger tied to the default 30 MPa band even when the per-move held-current correction cap is raised for a specific recipe.
- Mini DMA current sweeps now throttle the increasing-current ramp clock briefly after held-current recovery so the next thermal step does not immediately outrun stress recovery.
- Mini DMA current-sweep target ramps now stop as mechanical load loss/slack if the stage travels after l0 while measured load/stress remains near zero; this guard does not infer electrical contact loss because current may still be flowing.
- Added a 50 MPa iso-stress current-sweep recipe for 1 mA to 50 mA and back.
