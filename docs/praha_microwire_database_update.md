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

## Update Command

From the PyPlot repo root, on the current approved integration branch:

```powershell
$env:QT_QPA_PLATFORM='offscreen'
$env:MPLBACKEND='Agg'
$env:TEMP='C:\Users\Martin\PyPlot\artifacts\tool-temp'
$env:TMP='C:\Users\Martin\PyPlot\artifacts\tool-temp'
$env:UV_CACHE_DIR='C:\Users\Martin\PyPlot\artifacts\uv-cache'
.\.venv\Scripts\python.exe launcher.py --automation-recipe docs\automation_templates\praha_microwire_database_update.json
```

## Required Behavior

- The recipe refreshes the TMA section from
  `G:/My Drive/1 Projects/Praha/mini DMA`.
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
