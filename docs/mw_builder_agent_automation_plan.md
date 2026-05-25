# Microwire Builder Agent Automation Plan

This note tracks the implementation direction for making Microwire Data Builder safe to operate from Codex without opening the UI by hand.

## Goal

Support copy-safe, deterministic updates to `.pydpj` projects from new measurement data. A typical target flow is:

1. Copy the user's source `.pydpj` to a disposable working path.
2. Add or refresh a measurement section such as VSM Temperature Scan from one or more new files or folders.
3. Rebuild the affected section payloads from the copied project.
4. Save the updated copy and emit a structured manifest describing what changed.

The source project must remain untouched unless the user explicitly asks to overwrite it.

## Implemented In Phase 1

- `launcher.py --automation-recipe <recipe.json>` now accepts `kind: "builder"` recipes.
- The first command is `action: "update_section"` for `section: "vsm_temperature_scan"`.
- Builder section project payloads now embed parsed graph payloads as `pickle-base64`, so copied `.pydpj` files can restore graph records without the global AppData store.
- Automation runs patch the Builder storage root to a working-copy-local `_builder_store` folder while processing, so tests and CLI recipes do not depend on the user's real Builder cache.

## Remaining Blockers

- Assemble rebuild is still not part of builder automation v1.
- Other graph sections still need their own automation commands.
- Startup still imports the full Builder module and PyPlot plugin graph stack, although initial recursive pending scans are now suppressed during section construction.

## Project Payload Requirement

Before or alongside the first automation command, `.pydpj` saving must become self-contained for parsed records.

Preferred shape:

```json
{
  "sections": {
    "vsm_temperature_scan": {
      "columns": [],
      "rows": [],
      "payloads": {
        "vsm_temperature_scan_records": {
          "encoding": "pickle-base64",
          "value": "..."
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
    }
  ]
}
```

## Safety Rules

- Default to a copied project.
- Refuse to overwrite the input `.pydpj` unless an explicit overwrite flag is present.
- Resolve all paths relative to the recipe file before execution.
- Write diagnostics, manifests, and temporary files under ignored workspace or caller-provided output folders.
- Do not write to the user's real `.microwire_data_builder` store during tests; patch the storage root to a temporary path.

## Tests Added

- Loading a project copy with VSM Temperature Scan records works after clearing `MiniDatabaseStore` memory and pointing AppData storage at an empty temp folder.
- A builder automation recipe updates only the copied `.pydpj`, remains idempotent for repeated inputs, and reports skipped malformed VSM files in the manifest.

## Tests Still To Add

- Assemble rebuild can use the project-scoped VSM payload after the global store is empty.

## Later Phases

- Add commands for Mini DMA, current annealing, VSM hysteresis, DMA Iso-Stress, Shape Memory Stress/Strain, and FMR.
- Add a live Builder session bridge only if project-level commands are insufficient.
- Lazy-load heavy PyPlot and Origin dependencies during Builder startup so opening the UI does not import every plotting backend.
