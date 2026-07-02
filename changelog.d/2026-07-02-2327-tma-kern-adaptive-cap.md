2026-07-02 23:27

- Increased the Kosice KERN current-hold adaptive cap to a response-earned `1.25x` of the `0.08%` base strain cap after multi-seed simulator checks, keeping Prague/G&G control unchanged.
- Replaced the hidden fixed-MPa adaptive large-error floor with a band/readability/target-derived floor so current-hold recovery scales across wire geometries.
- Updated KERN simulator and documentation wording to use the observed approximately 16 Hz full-session raw scale cadence rather than idealized 20 Hz feedback.
