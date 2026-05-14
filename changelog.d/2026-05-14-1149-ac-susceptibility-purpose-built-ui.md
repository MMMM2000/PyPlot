2026-05-14 11:49

- Refined the AC Susceptibility Logger UI so the sticky actions, point-acquisition labels, filenames, and live plots are specific to empty-coil baseline and microwire current-sweep workflows.
- Updated OWON SPE6102 AC sweep defaults to a 62 V voltage limit and migrated older 5 V/60 V OWON defaults when OWON is selected.
- Replaced the quick AC plot selectors with a Mini-DMA-style configurable plot dashboard, with `Rs vs DC current` and `Ls vs DC current` as the default live views.
- Removed startup PSU auto-detection from normal launch so safe serial `*IDN?` probing only runs when Auto-detect instruments is requested.
