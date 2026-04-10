2026-04-10 09:28
- Microwire EDA now prefers copied .pydpj project files, supports raw/per-wire-median/per-wire-best repeated-measurement analysis modes, and writes those choices into report artifacts.
- EDA now derives geometry helper metrics, parses elemental composition columns, and adds dedicated current- and composition-side correlation sections to the report.
- Auto-findings now prefer controllable fabrication signals when summarizing process-to-outcome trends.
- Aggregated EDA modes now preserve rows missing a complete `Composition + Microwire` key, and legacy mojibake diameter headers still map into the canonical geometry columns.
- Fabrication source relabeling now preserves annealing provenance for dual-source wires and no longer fails when older payloads omit the `Data source` column.
