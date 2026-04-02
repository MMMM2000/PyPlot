2026-03-24 22:06

- Refactored Microwire EDA into a single canonical analysis pipeline with explicit `run_analysis`, `write_analysis_artifacts`, and compatibility `generate_report` entry points.
- Added copy-safe `.pydpj` analysis for CLI and agent workflows, including findings JSON/Markdown outputs, manifest tracking of the disposable project copy used for the run, and transient Assemble rebuilds from Builder project sections when needed.
- Reframed Microwire EDA around modern measured strain and fracture endpoints, with legacy broke/OK analysis retained only as optional auxiliary context.
- Added composition-split signal tables so cross-composition trends can be compared against per-composition endpoint behavior.
- Added `docs/microwire_eda.md` and updated Builder docs to describe the autonomous workflow, RF_EDA alignment, and copy-before-analysis rule.
