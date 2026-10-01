# Praha Microwire Database Update

Use this workflow when updating the live Praha Microwire Data Builder database.
Do not hand-edit the live `.pydpj` and do not export DOCX reports until the
update manifest has been inspected.

## Source Of Truth

- Live latest project:
  `G:/My Drive/1 Projects/Praha/microwire_database/microwire_database_latest.pydpj`
- Previous latest projects are archived automatically under:
  `G:/My Drive/1 Projects/Praha/microwire_database/archive/`
- Working copies are written under:
  `G:/My Drive/1 Projects/Praha/microwire_database/_working/`
- Reusable recipe template:
  `docs/automation_templates/praha_microwire_database_update.json`

There is one canonical latest database. `_working/<UTC-run-id>/` is temporary
staging, not a competing latest or the normal place to save scientific reviews.
Older backups belong in `archive/`; preserve user edits and avoid overwriting
archive names when organizing legacy files.

## Complete Source Inventory

Unless the user limits the import, check all supported source families before
generating the update commands:

| Family | Sources to check |
| --- | --- |
| VSM temperatures and hysteresis | `Praha/data/VSM temp scan`, `Praha/data/VSM hysteresis loops`, new USB data |
| TMA | `Praha/data/TMA`, supported run folders in `Praha/data/elastrocaloric_effect`, and saved project roots |
| Current annealing / resistance-current | `Praha/data/current annealing`, the saved Kosice Current Annealing shared-drive root, `Praha/data/vacuum`, and standalone current-program sessions |
| Fabrication and videos | Saved `databaza mikrodrotov` shortcut target, new workbooks and recordings |
| Microscopy | `Praha/data/microscope`, saved individual files and manual diameter overrides |
| DMA, FMR, manual stress/strain | `Praha/data/DMA`, `Praha/data/FMR`, `Praha/data/manual stress-strain` |
| Other data | Inventory newly discovered folders and verify importer support; preserve saved strain/transition values |

Report every family as updated, unchanged, unavailable or awaiting evidence.
Metadata discovery alone does not establish content identity or completed runs.
The `mw data sheets` folder contains report exports; do not treat DOCX files as
source fabrication spreadsheets. New videos require reviewed readings before
they can supply new production parameters.

The installed `$praha-microwire-database` skill captures the full workflow and
historical evidence. Its source-inventory helper checks all supported families;
its strict VSM validation/promotion helper is specifically for VSM-only candidates.

Resistance/current sessions with `current_program_logger_v1` metadata use the
explicit composition and draw/piece in the run-folder name. The importer does
not assign unidentified `current-program_*` runs from nearby files, turn a
supply current limit into a treatment setpoint, or infer a numerical pressure.
Record skipped empty runs and unresolved sample identities in the update report.
TMA completion gating applies across all refreshed supported roots.

Fabrication and Video automation restore saved annealing/microscopy dependencies
for sample relevance. A shared draw number alone is not a material match.
Reference-only video refreshes retain numerical readings and source history;
new readings still require the frame-review procedure.

## Stage, Validate, Promote, Review

1. Snapshot the canonical latest and its manifest hashes and create a verified
   input copy under a unique `_working/<UTC-run-id>/` directory.
2. Generate explicit copied `project`, `output_project`, `manifest_path` and
   `working_copy_dir` recipe paths, omitting `database_dir` during staging.
   Refresh new/changed families and their dependencies, with Fabrication before
   Videos and Assemble last. Use isolated Builder stores/settings.
3. Check the saved candidate's actual records/arrays, existing sample/source
   groups, hidden/excluded state, manual reviews and overrides, unrelated
   sections, Assemble identities and manifest counts. Do not validate from
   status=ok or table rows alone.
4. Recheck the original latest and manifest hashes. Archive the previous versions
   and promote the same validated candidate with the guarded application helper.
   If a newer user save exists, stop and reconcile it.
5. Open `microwire_database_latest.pydpj` with the fixed Builder for normal
   scientific review. Snapshot a substantial review session into `archive/`
   before editing. Save accepted/excluded decisions through the app; import
   success does not establish scientific acceptance.

Review validation must compare scientific decisions, including accepted-auto and
manual-adjusted sidecars with identical final points. Existing conflict history
must not grow another wrapper merely from opening/importing the same sidecar.
After correcting review metadata, rebuild Assemble so its values and statuses
correspond to the same final reviews. A console encoding failure after saving is
not proof that the package failed; inspect and validate the saved artifacts before
retrying an update or promotion.

## Update Command

The supplied rolling recipe is a **TMA-only auto-promoting template**, not a full
database update or a staged validation recipe. For a general update, generate
batch-specific commands from the complete source inventory above. Use the
rolling template only within its intended scope after validating the candidate.
From the fixed PyPlot checkout, with existing ASCII temp/cache directories:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:MPLBACKEND='Agg'
$env:TEMP='C:\Users\Martin\PyPlot\artifacts\tool-temp'
$env:TMP='C:\Users\Martin\PyPlot\artifacts\tool-temp'
$env:UV_CACHE_DIR='C:\Users\Martin\PyPlot\artifacts\uv-cache'
uv run --frozen python launcher.py --automation-recipe <staged-recipe.json>
```

## Required Behavior

- The recipe refreshes the TMA section from
  `G:/My Drive/1 Projects/Praha/data/TMA`.
- It ignores `archive`, `automation_history`, `automated_control_tests`, and
  `automated` folders.
- TMA import is sample-gated: if the newest active run for a sample is not
  finished, no older run for that same sample is imported as a fallback.
- After refreshing TMA, the recipe rebuilds Assemble from the project
  sections.
- The automation archives the previous `microwire_database_latest.pydpj` before
  promoting the newly rebuilt latest project.

## After The Run

Inspect `update_manifest_latest.json` and confirm:

- `status` is `ok`.
- `database.archived_project` points to the previous latest project.
- `database.latest_project` points to the promoted latest project.
- The TMA command reports the expected source path and row count.
- Assemble rows were rebuilt.

Only after this manifest check should DOCX export be considered. For TMA
transition-current work, manually review the Builder transition review UI before
treating extracted As/Af/Ms/Mf values as final report values.
