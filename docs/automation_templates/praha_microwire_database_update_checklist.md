# Praha Microwire Database Update Checklist

Use this checklist whenever refreshing the shared `.pydpj` database before DOCX export. The goal is to make each refresh reproducible and to record when fabrication data is not available yet.

1. Start from `microwire_database_latest.pydpj` or the latest approved integration copy. Archive the previous latest project and manifest before promoting a new latest project.
2. Check for new fabrication spreadsheets first. If matching files exist, refresh the Fabrication section before rebuilding Assemble. If no matching files are available yet, record `fabrication pending/not uploaded` in the handoff and update manifest notes.
3. Run the supported automation recipe for measurement sections, for example:

   ```powershell
   uv run python launcher.py --automation-recipe docs/automation_templates/praha_microwire_database_update.json
   ```

4. Confirm the recipe manifest includes the intended updated sections and `rebuild_assemble`.
5. Open the copied project, not the original live project, and spot-check Current annealing, Fabrication, Mini DMA, Assemble, and any newly changed graph sections.
6. Only after the copied project is correct, promote it to `microwire_database_latest.pydpj`.
7. Export DOCX reports from the promoted project only after the Mini DMA and current-annealing transition reviews are accepted for the relevant samples.

Current limitation: `update_section` does not yet support Fabrication because Fabrication keeps draw-level and piece-level index payloads in addition to visible rows. Until a dedicated fabrication automation command is added, refresh Fabrication via the Builder UI or a dedicated helper and note the result next to the automation manifest.
