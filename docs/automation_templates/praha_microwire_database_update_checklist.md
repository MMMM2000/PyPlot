# Praha Microwire Database Update Checklist

Use this checklist whenever refreshing the shared `.pydpj` database before DOCX export. The goal is to make each refresh reproducible and to record when fabrication data is not available yet.

1. Start from `microwire_database_latest.pydpj` or the latest approved integration copy. Archive the previous latest project and manifest before promoting a new latest project.
2. Check for new fabrication spreadsheets first. If matching files exist, add a `fabrication` update before rebuilding Assemble. Partial updates retain existing draw/piece records and saved fields absent in the new spreadsheets. If no matching files or verified video readings are available yet, record `fabrication pending/not uploaded` in the handoff and update manifest notes. Read pressure units from the source evidence; do not infer them from an unlabelled number.
3. First make a review recipe with explicit `project`, copied `output_project`, and `working_copy_dir`, omitting `database_dir`. The supplied rolling template uses `Praha/data/TMA` and promotes automatically; use it after reviewing the copied result:

   ```powershell
   uv run python launcher.py --automation-recipe docs/automation_templates/praha_microwire_database_update.json
   ```

4. Confirm the manifest includes the intended updated sections and `rebuild_assemble`. Check record counts, skipped sources and retained skipped records. Keep graph `prune_missing` disabled unless deliberately replacing the supplied roots. TMA uses its existing newest-active-run selection.
5. Open the copied project, not the original live project, and spot-check Current annealing, Fabrication, TMA, Assemble, and any newly changed graph sections.
6. Only after the copied project is correct, promote it to `microwire_database_latest.pydpj`.
7. Export DOCX reports from the promoted project only after the TMA and current-annealing transition reviews are accepted for the relevant samples.

Video ranges retain fractional metres and require all preceding piece lengths. A missing range indicates incomplete or inconsistent length evidence; resolve it before extracting production parameters. Video automation preserves existing readings and references, but new readings still require frame review. Scientific acceptance of transition choices and live Word/Origin rendering remains separate from software verification.
