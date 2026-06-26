2026-06-23 14:05

- Added a software-only TMA full-run simulator for first-overheating style target acquisition, current rise, endpoint recovery, reverse unwind, bounded mechanical corrections, delayed scale feedback, and slack take-up.
- Added a calibrated realistic 50 MPa good-wire simulation that emits full measurement-style logs, stress recovery metrics, current-hold timing, and high-strain strain-current plots comparable to completed `Ni50Fe27Ga23 12/2` TMA optimization runs.
- Changed the realistic full-run strain model so strain-current plots are calculated from simulated motor motion instead of artificial current/transformation strain shaping; current holds now keep current fixed while strain changes only through controller-driven mechanical correction.
- Recalibrated the realistic full-run transformation driver against real 50 MPa run34 hold spans, using stepped transformation kinetics so fixed-current holds can move strain by realistic multi-percent amounts without artificial oscillatory strain forcing; fixed the current/motor plot to use separate mA and mm axes.
- Added full-run scenario reports and a parameter sweep that emit machine-readable summaries plus plots under `artifacts/`.
- Bounded processed-noise admission so very broad raw stress envelopes cannot hide a processed center that is materially off target.
