2026-06-17 15:23

- Let Mini DMA current-hold recovery cautiously grow above one motor Tic when an unstable hold is still showing persistent same-sign improvement, while keeping one-Tic damping for reversals and worsening responses.
- Treat same-sign current-hold stress drift away from target as bounded dynamic recovery instead of oscillation, record filtered slope/noise in the control trace, and show active current-sweep progress from the current fraction rather than an exhausted nominal tick count.
- Add an optional bench-plan current-hold quality watchdog for optimization runs so clearly bad candidates can stop early with explicit stop metadata.
- Honor the Mini DMA current-sweep `reverse_current` recipe flag so optimization recipes can run a one-way current ramp while keeping the default sweep-back behavior and voltage-limit unwind safety.
- Add a reusable Mini DMA stiff-sample guard CLI that regenerates offline evidence for stiffness-scaled current-hold drift recovery and historical oscillation clamps before stiff-wire hardware validation.
