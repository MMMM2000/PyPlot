# Database Builder Overview

Microwire Data Builder aggregates per-section measurements (fabrication, annealing, microscope, etc.) into a single combined dataset that can be previewed, filtered, and exported. This document captures expected behaviors so we can test against a consistent spec.

For the dedicated manual fabrication-video workflow, see [Universal Video Builder](./video_builder.md). That tool is separate from the full Microwire Data Builder and keeps fabrication/video review in a single focused window.

## Sections and Data Sources

- Fabrication: metadata columns (length, datetime, mass, resistance, notes), computed e/a ratio, plus any available video-related fields (core/glass temperature, winding speed, glass feeding, underpressure). Imported samples are added here so fabrication lookups work even when no spreadsheet exists yet.
- Current annealing: uses one dedicated `1000 mA` anchor slot plus an aggregated `Other annealing` bucket for every non-anchor run; extra runs surface in "Figure — other annealing" and can be hidden via the visibility dialog.
- Microscope: d/D values, images, and review state; reviewed values must not be deleted on refresh.
- Current density: As/Af/Ms/Mf markers (two passes if measured twice).
- Videos: shows the fabrication-style table for video-linked samples, supports manual edits for fabrication fields, and adds `Video end length (m)` + derived `Video microwire length (m)` (baseline minus cumulative Length (m) for the draw). Edits override assembled values. Videos can be opened from the selection.
  Video review state: required empty fields stay red until filled, first-time fills stay green, and overwriting an existing value highlights the cell amber with hover access to the prior value plus a restore action from the review dialog. `Notes` stays neutral instead of being treated as a required review field.
- VSM hysteresis: multiple graphs per sample row (angles grouped per temperature, sub-versions in the same row).
- VSM temperature scan: multiple graphs per sample row (sub-versions in the same row).
- Transition temps: pick As/Af/Ms/Mf from VSM temperature graphs without switching away from the active graph.
- DMA iso-stress: multiple graphs per sample row (sub-versions in the same row).
- Shape memory stress/strain: multiple dual-axis overlay graphs per sample row from manual stress/strain logger TXT files. The section includes an optional side preview panel; double-clicking a preview picks displacement/load/strain/stress values into dedicated columns that can be carried into Assemble, and the picker can target either the standard shape-memory columns or the fracture load/strain/stress columns.
- Section search: all builder tables now expose a search box that filters rows across visible columns, including the graph/data sections and the custom Current density, Transition temps, and Compare views.
- Microscope other ends: filenames with an `oe` suffix (for example `Ni46Fe23Ga23Co8 1-1oe core`) are treated as separate samples, and the Microscope tab can show or hide those `oe` rows without deleting them.
- FMR: Field vs X/Y voltage plots from CSV files; multiple graphs per sample row.
- Strain: stress/strain entries tied to composition + microwire; d auto-filled from microscope when available.
- Assemble: combined preview and export configuration.
- Analysis: `Analysis -> Analyze assemble data...` opens the separate Microwire EDA tool using the current filtered Assemble rows. The report generator is read-only, analyses only data already present in Assemble, shows a progress dialog while it runs, and writes into a dedicated report subfolder. When launched from a `.pydpj` path directly, the autonomous CLI flow now uses a disposable copied project file by default so the source project is not mutated during analysis.
- Assemble imports: spreadsheet rows can be imported and merged with the assembled dataset; imported rows are tagged via the "Data source" column and enriched with fabrication metadata where possible.
- Data menu: import external workbooks, toggle visibility of imported-only rows, or remove imported data entirely. Optionally separate imported Fabrication rows under an "Imported data:" divider. Imported workbooks appear under Project Explorer.
- Compare: subset of rows for side-by-side comparison.

## Sample Naming and Grouping

- Sample identification is always the leading composition + draw-piece pair in the folder/file name, e.g. `Ni50Fe27Ga23 5-4` -> Composition `Ni50Fe27Ga23`, Microwire `5/4`.
- Suffixes appended directly to the draw/piece (for example `10-5oe`) become part of the microwire key and create a separate sample row.
- Extra suffixes after the microwire token (e.g. `no glass`, `NG CA`, `s1`, `s3`) define sub-versions for the same sample; they should appear as additional graphs in the same row.
- One row per sample in the VSM/DMA sections; multiple graphs for the same sample are placed side-by-side in that row.
- Hidden graphs (visibility dialogs) are excluded from Assemble/Compare/exports and from HTML compare output.
- Visibility dialogs support per-graph group toggles (for example, hide/show an entire temperature group in VSM).
- Graph titles should include the suffix label (for example, `Ni50Fe27Ga23 10/5 (NG CA)` or `Ni50Fe27Ga23 11/1 (s3)`).

## Current Density Requirements

- Support two measurement passes: As1/Af1/Ms1/Mf1 and As2/Af2/Ms2/Mf2.
- Derived columns include the deltas between passes (As2-As1, Af2-Af1, Ms2-Ms1, Mf2-Mf1) and Mf1-Af1 / Mf2-Af2.
- Provide the current density columns (A/mm^2) for each transition when diameter is known.
- Respect the active selection (e.g. editing Af1 should not overwrite As1).
- Assemble preview/export should include the As*/Af*/Ms*/Mf* columns even when they originate from the current density/phase-point inputs.

## Microscope + Strain Workflow

- Manual microscope entries are preserved; once a value is reviewed it must remain on refresh, and review color should track the value.
- Keyboard flow: Tab moves between d and D cells within the table; Enter should advance to the next editable field without jumping to the start.
- Strain entries:
  - d should default from microscope when available.
  - Weight and stress are linked; editing one should recalculate the other.
  - Export stress, weight, and mode (single vs dual span) to the database.
  - Enter submits the entry; Tab cycles between input fields.

## Assemble (Preview + Export)

- Export is driven by one button: open settings, adjust, then export.
- "Preview database" shows the combined view using current column selection and sorting; "Combine database" is the full merge for export outputs.
- `Analyze...` opens the standalone Microwire EDA workflow. When a preview is available it uses the current filtered Assemble rows as its default scope, keeps the current selected subset available as an alternate scope, and writes a read-only HTML/Excel/CSV/manifest report bundle without modifying the `.pydpj` project.
- Column selector must include every column from every section, including graph columns; graph columns are off by default.
- Column order, visibility, and multi-column sort are persisted in the `.pydpj` project.
- Assemble preview includes a quick search box that filters rows across currently visible columns; saved projects keep the active search text.
- Microwire sorting is numeric (10/5 comes after 5/4).
- Graph preview panel is optional. When enabled, selecting a row shows current annealing, VSM, DMA, shape-memory, and FMR previews for that row.
- "Add to compare" uses the current row selection and populates the Compare tab.
- Multi-graph previews in non-Compare sections are laid out horizontally; Compare stacks graphs vertically with rows tall enough to show full-size plots.
- The bottom-right `Export worksheet...` shortcut was removed; use the top-left `Export...` flow for Assemble exports.

## Compare

- Holds the selected rows from Assemble.
- Default view is a samples-as-columns matrix: columns are `Composition Microwire`, rows are selected fields, and graph rows render inline previews for side-by-side comparison. Selecting any cell highlights the full field row.
- Users can switch back to the row-based view and choose which fields to show + their order.

## Export Targets

- CSV, Excel, HTML (self-contained) outputs are supported.
- Word sample reports can be exported from Assemble as one `.docx` per sample row. The reports use a fixed sample-report template: sample summary, fabrication values, functional values, microscope dimensions/images, then graph sections for current annealing, R vs T, VSM temperature scans, VSM hysteresis loops, DMA iso-stress, shape-memory stress/strain, and FMR. Every graph section is present even when a sample has no measurement yet.
- Word sample reports embed any generated Origin graph object files as editable Word OLE objects when Microsoft Word automation is available. Graph families whose editable Origin export is not implemented yet are still listed from Assemble so the report structure stays stable.
- Word sample reports can also be generated without opening the Builder UI: `launcher.py --microwire-word-report <project.pydpj|assembled.xlsx|RvsT.csv> [--microwire-word-sample "Ni50Fe27Ga23 12/2"] [--out <dir>]`. Project inputs merge the saved Builder section rows directly so reports can include samples that are present in measurement sections even when the saved Assemble table is stale, and they discover matching R-vs-T CSVs under a sibling `RvsT` folder. Direct R vs T CSV inputs create a sparse sample report with an embedded R-vs-T Origin OLE graph object when Origin and Word automation are available.
- HTML export must not require the Videos section to be processed, and includes a compare view (Ctrl/Cmd-click rows to compare).
- Matplotlib/Origin outputs should use the same rows and columns as the Assemble preview.
- No PyPlot UI should be embedded inside the builder; graphs are rendered as static previews like the current annealing section.
- Microwire EDA outputs are separate from Assemble exports: the EDA tool writes its own HTML report, summary workbook, canonical CSV, manifest JSON, and optional figure bundles to a dedicated subfolder under the chosen report directory.

## Microwire EDA

- Microwire EDA is a separate read-only workflow that can load either a Builder `.pydpj` project or an assembled spreadsheet export.
- The current analysis is endpoint-first rather than broke/OK-first. Modern measured endpoints (`Strain (%)`, `Fracture strain (%)`, `Stress (MPa)`, `Fracture stress (MPa)`) are the primary outputs; legacy broke/brittle labels are retained only as optional auxiliary context.
- For `.pydpj` inputs, Microwire EDA now prefers a copy-safe autonomous flow and can rebuild Assemble rows transiently from the Builder project sections when the saved Assemble payload is missing or when a force-rebuild run is requested.
- The generated bundle now includes the HTML report, summary workbook, canonical CSV dataset, findings JSON, findings Markdown brief, and manifest JSON. Findings focus on what the data currently supports and which follow-up experiments are worth running next.
- Standalone command entrypoint: `launcher.py --microwire-eda <project.pydpj|assembled.xlsx> [--rows all|filtered|selected] [--out <dir>] [--microwire-eda-title <title>] [--microwire-eda-working-copy-dir <dir>]`.
- Builder-launched analysis uses the current Assemble preview rows directly when available so the report matches the visible filtered subset rather than silently reverting to the full project.
- See `docs/microwire_eda.md` for the full autonomous workflow, copy-safe rules, and RF_EDA reference alignment.

## Logging and Diagnostics

- Message Log captures warnings/errors for builder actions.
- Developer menu includes "Capture Message Log to File" to write to `logs/message_log.txt`.
- Unread errors should highlight the Message Log dock/button.
