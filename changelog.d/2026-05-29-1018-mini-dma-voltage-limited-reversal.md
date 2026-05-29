2026-05-29 10:18

- Prevent voltage-limited Mini DMA current sweeps from jumping back to the nominal maximum current after the unwind leg; the unwind is kept as the shortened return leg and logged explicitly.
- Let fast moving-away current-sweep stress/load errors enter current hold immediately instead of waiting through the normal confirmation delay.
- Let clearly large current-hold recovery errors bypass the persistence timer, while keeping persistence gating for smaller filtered errors.
