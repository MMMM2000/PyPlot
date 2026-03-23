2026-03-23 12:48
- Simplified Microwire Data Builder current annealing rows to use one `1000 mA` anchor slot plus one aggregated `Other annealing` bucket.
- Current annealing previews, worksheet export, assemble/compare graph previews, and HTML export now show `Other annealing` instead of separate low/other mA buckets.
- Builder exports now keep all non-anchor annealing files and figures together, while preserving deterministic exact-`1000 mA` anchor selection and warning when the anchor is missing.
