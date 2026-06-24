2026-06-23 20:05

- Mini DMA full-run simulation reports now expose hidden free transformation strain, motor-derived measured strain, elastic mismatch, and measured-vs-free strain tracking error so controller changes can be judged against the modeled wire contraction/elongation instead of stress error alone.
- The full-run simulator now includes a broad free-strain stress-test matrix with real-run-inspired good, early 19/8, bad Co6, and weak/noisy wire families, physical free-strain roughness during transformation, delayed-feedback variants, and JSON/CSV/Markdown/PNG matrix summaries.
- A representative Mini DMA control-policy matrix now compares geometry-percent correction caps and target-fraction recovery bands across good high-strain and weak/noisy simulated wires before changing live control behavior.
- Full-run simulations can now exercise multi-target stress ladders, including a 0 -> 50 -> 100 MPa good-wire scenario with post-unwind free-length/slack disturbance before the second target ramp.
