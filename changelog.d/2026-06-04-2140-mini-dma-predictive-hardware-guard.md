2026-06-04 21:40
- Added an experimental Mini DMA current-sweep no-conduction readback guard for predictive controller hardware runs.
- Disabled the live predictive ramp clock after hardware evidence showed worse stress control; predictive tooling remains available for replay analysis only.
- Added a predictive 0.4 mA/s iso-stress recipe that pins the accepted 0.5 s current-hold resume confirmation.
