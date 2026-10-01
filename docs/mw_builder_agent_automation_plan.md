# Microwire Builder Agent Automation Plan

This note tracks the implementation direction for making Microwire Data Builder safe to operate from Codex without opening the UI by hand.

## Goal

Support copy-safe, deterministic updates to `.pydpj` projects from new measurement data. A typical target flow is:

1. Copy the user's source `.pydpj` to a disposable working path.
2. Add or refresh a measurement section such as VSM Temperature Scan from one or more new files or folders.
3. Rebuild the affected section payloads from the copied project.
4. Save the updated copy and emit a structured manifest describing what changed.

The source project must remain untouched unless the user explicitly asks to overwrite it.

## Implemented

- `launcher.py --automation-recipe <recipe.json>` now accepts `kind: "builder"` recipes.
- `action: "update_section"` supports `fabrication`, `videos`, `microscope` and graph-backed sections including `annealing`, `vsm_temperature_scan`, `vsm_hysteresis`, `dma_iso_stress`, `mini_dma`, `shape_memory_stress_strain`, and `fmr`.
- `action: "rebuild_assemble"` refreshes saved Assemble rows from the copied project's embedded section payloads without opening the full Builder UI.
- Version 3 projects use a ZIP package containing safe `microwire-json` metadata and content-addressed numeric blobs. Legacy executable pickle payloads are blocked on normal reads; trusted-copy migration is a separate explicit operation.
- Automation uses unique working-copy-local store and payload staging folders, then removes only its own temporary folders.
- `database_dir` recipes maintain a rolling latest project and manifest, prepare recovery copies, reject concurrent changes, and roll back the project if a later promotion step reports failure.
- Refreshes preserve unparsed existing curves and saved VSM grouping by default. `prune_missing` is an explicit graph-only replacement option. Fabrication refreshes merge both visible and raw indexes; video refreshes preserve saved readings and use project Fabrication data for length mapping.
- Builder startup can be configured to open the latest project from that database folder instead of the older last-opened project.

## Remaining Work

- Startup still imports the full Builder module and PyPlot plugin graph stack, although initial recursive pending scans are now suppressed during section construction.
- A live Builder session bridge may still be useful later, but project-level automation should stay the default path unless interactive UI state is required.

## Project Payload Requirement

Version 3 `.pydpj` packages are self-contained for parsed records and their numeric data.

Preferred shape:

```json
{
  "sections": {
    "vsm_temperature_scan": {
      "columns": [],
      "rows": [],
      "payloads": {
        "vsm_temperature_scan_records": {
          "encoding": "microwire-json",
          "version": 1,
          "value": {"$type": "list", "items": []}
        }
      }
    }
  }
}
```

The exact encoding can change, but the invariant should not: loading a `.pydpj` copy must not require unrelated AppData payloads to recreate graph records.

## Recipe Sketch

```json
{
  "kind": "builder",
  "version": 1,
  "project": "microwire_project.pydpj",
  "working_copy_dir": "artifacts/builder_automation",
  "output_project": "artifacts/builder_automation/microwire_project.updated.pydpj",
  "commands": [
    {
      "action": "update_section",
      "section": "vsm_temperature_scan",
      "paths": ["new_vsm_temp_scan_folder"]
    },
    {
      "action": "rebuild_assemble",
      "sections": ["vsm_temperature_scan"]
    }
  ]
}
```

For the Praha rolling database workflow, use `database_dir` instead of fixed
`output_project` and `manifest_path` fields:

```json
{
  "kind": "builder",
  "version": 1,
  "database_dir": "G:/My Drive/1 Projects/Praha/microwire_database",
  "commands": [
    {
      "action": "update_section",
      "section": "mini_dma",
      "paths": ["G:/My Drive/1 Projects/Praha/data/TMA"]
    },
    {
      "action": "rebuild_assemble",
      "sections": ["mini_dma"]
    }
  ]
}
```

The database folder keeps `microwire_database_latest.pydpj` and
`update_manifest_latest.json` at the root, and moves the previous latest files
to timestamped copies in `archive/`. For a review-only dry run, omit `database_dir`
and set `project`, `working_copy_dir`, and a distinct `output_project` explicitly.

## Safety Rules

- Default to a copied project.
- Refuse to overwrite the input `.pydpj` unless an explicit overwrite flag is present.
- Resolve all paths relative to the recipe file before execution.
- Write diagnostics, manifests, and temporary files under ignored workspace or caller-provided output folders.
- Do not write to the user's real `.microwire_data_builder` store during tests; patch the storage root to a temporary path.

## Tests Added

- Loading a project copy with VSM Temperature Scan records works after clearing `MiniDatabaseStore` memory and pointing AppData storage at an empty temp folder.
- A builder automation recipe updates only the copied `.pydpj`, remains idempotent for repeated inputs, and reports skipped malformed VSM files in the manifest.
- Current annealing and the other graph-backed sections are supported by the same `update_section` command family.
- Assemble rebuild can use copied project payloads, including TMA summaries, without constructing the full Builder window.
- A builder automation recipe with `database_dir` promotes a new latest project/manifest and archives the previous latest files with a timestamp.

## Later Phases

- Add a live Builder session bridge only if project-level commands are insufficient.
- Lazy-load heavy PyPlot and Origin dependencies during Builder startup so opening the UI does not import every plotting backend.
