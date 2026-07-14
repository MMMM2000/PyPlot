2026-07-14 10:14

- Made Microwire Data Builder project opening responsive and transactional, with lazy CA/VSM/TMA transition workspaces that preserve reviewed and explicit `No transition` states.
- Replaced ordinary Builder project/store pickle decoding with a bounded, versioned JSON codec and an explicit trusted legacy-copy migration workflow.
- Stabilized sample identity and public export semantics, including `No1`/`noload` grouping, separately hideable `oe` rows, per-target TMA values, current density, strain baselines, and user-selected Analysis column visibility/order.
- Kept XLSX and DOCX boss-facing output aligned to one source-neutral Analysis sheet/table while retaining reviewed data and embedded report media.
- Improved Builder high-DPI layout, transition navigation, accessibility labels, and consistent Unicode labels for `Ω`, `µm`, and `A/mm²`.
