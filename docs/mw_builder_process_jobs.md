# MW Builder Process Jobs

This note defines the first process-separation boundary for Microwire Data Builder
and PyPlot automation. The goal is to keep the manual UI responsive while giving
Codex and batch workflows a repeatable, inspectable way to run long jobs.

## Audit Summary

Heavy flows currently fall into five groups:

- Section refreshes over large folders: Builder sections already use `QThread`
  workers for scan/import work, but they still share the UI process, memory,
  logging, and cancellation model.
- TMA previews and transition review: recent work moved transition review
  to lazy selected-run loading with a background worker. This should remain
  in-process for interactive review, while expensive pack generation can later
  become a job.
- Assemble rebuilds: `launcher.py` has a lightweight `rebuild_assemble`
  automation path inside Builder recipes. This is a good second subprocess
  candidate because it is deterministic and produces a project/manifest.
- Word DOCX export: `launcher.py --microwire-word-report` performs source
  loading, optional project rebuild, Origin graph generation, DOCX creation,
  Word image insertion, Word OLE insertion, and manifest writing synchronously.
- Origin graph generation and Word embedding: these are the most fragile and
  slowest pieces because they involve Origin automation, the clipboard, Word
  COM, and PowerShell subprocesses. Real embedded Origin OLE objects remain
  required for final DOCX output.

## Recommended Boundaries

1. **First: Microwire Word/DOCX export job.**
   This is the best first subprocess boundary. It is already CLI-driven, has
   clear inputs and outputs, touches Origin/Word COM, can run for a long time,
   and benefits most from external progress/status files.

2. **Second: Builder database update and Assemble rebuild job.**
   The Builder recipe path already writes a manifest and uses a copied project.
   A job wrapper should add progress, cancellation markers, and a consistent
   status schema.

3. **Third: heavy graph preview and review-pack generation.**
   TMA transition review should keep its interactive dialog in-process,
   but precomputing thumbnails, candidate packs, and diagnostic figures can
   move to cacheable subprocess jobs.

4. **Later: broad PyPlot plugin exports.**
   Plugin exports should not all move out-of-process at once. Keep ordinary
   plots in-process; move only batch exports that require Origin, many files, or
   long cold-start automation.

Keep short UI operations, table filtering, single-row preview rendering, and
manual review navigation in-process. They need low latency and direct widget
state.

## Job Contract

Process jobs use JSON request files and write JSON artifacts that can be
monitored by either the UI or Codex.

Request shape:

```json
{
  "version": 1,
  "job_type": "microwire_word_export",
  "job_id": "praha_docx_export",
  "source": "G:/My Drive/1 Projects/Praha/microwire_database/microwire_database_latest.pydpj",
  "output_dir": "G:/My Drive/1 Projects/Praha/microwire_database/docx_exports",
  "sample": "Ni50Fe27Ga23 12/2",
  "include_origin": true,
  "force_project_rebuild": true,
  "graphs_only": false,
  "dry_run": false,
  "paths": {
    "status": "artifacts/mw_jobs/praha_docx_export/status.json",
    "progress": "artifacts/mw_jobs/praha_docx_export/progress.json",
    "manifest": "artifacts/mw_jobs/praha_docx_export/manifest.json",
    "log": "artifacts/mw_jobs/praha_docx_export/job.log",
    "cancel": "artifacts/mw_jobs/praha_docx_export/cancel.requested"
  }
}
```

Status shape:

```json
{
  "kind": "pyplot_job_status",
  "version": 1,
  "job_type": "microwire_word_export",
  "job_id": "praha_docx_export",
  "state": "running",
  "step": "export",
  "message": "Starting existing Microwire Word export path.",
  "updated_at": "2026-06-12T12:00:00+00:00",
  "pid": 12345,
  "source": "...",
  "output_dir": "...",
  "dry_run": false
}
```

Progress shape:

```json
{
  "events": [
    {
      "time": "2026-06-12T12:00:00+00:00",
      "event": "started",
      "step": "validate",
      "message": "Microwire Word export job accepted.",
      "fraction": 0.0
    }
  ]
}
```

Manifest shape:

```json
{
  "kind": "pyplot_job_manifest",
  "version": 1,
  "job_type": "microwire_word_export",
  "job_id": "praha_docx_export",
  "state": "succeeded",
  "exit_code": 0,
  "source": "...",
  "output_dir": "...",
  "status_path": "...",
  "progress_path": "...",
  "log_path": "...",
  "cancel_path": "...",
  "equivalent_command": ["python", "launcher.py", "--microwire-word-report", "..."]
}
```

Error shape:

```json
{
  "error": {
    "type": "RuntimeError",
    "message": "Origin automation failed",
    "user_message": "Microwire Word job failed. See status JSON for details.",
    "traceback": "..."
  }
}
```

Cancellation is requested by creating the configured `cancel` path. The current
prototype checks the marker before starting export. Future work should check it
between frame loading, Origin graph generation, each DOCX, each Word embedding
batch, and manifest writing.

## CLI Prototype

The first wrapper is:

```powershell
uv run python launcher.py --microwire-word-job docs/automation_templates/microwire_word_job.json
```

When `dry_run` is true, the command validates the request and writes
status/progress/manifest files without generating DOCX files or starting
Origin/Word automation. When `dry_run` is false, it delegates to the existing
`--microwire-word-report` path, preserving real embedded Origin OLE behavior.

## UI Monitoring Model

The Builder UI should start jobs by launching the current Python interpreter in a
subprocess with `launcher.py --microwire-word-job <job.json>`. The UI should then
poll `status.json` and `progress.json`, stream `job.log`, and create the cancel
marker when the user presses Stop. The UI process should not need to import
Origin or Word COM for this flow.

Codex can run the same command noninteractively, inspect the JSON artifacts, and
resume diagnosis from the manifest paths.

## Remaining UI Work

The prototype is ready for a focused UI integration worker, but it is not yet
used by the Assemble DOCX export button. The next implementation should:

- generate a job JSON from the current export dialog choices,
- launch `launcher.py --microwire-word-job <job.json>` through `QProcess`,
- poll and render `status.json`, `progress.json`, and `job.log`,
- make Stop create the configured cancel marker,
- add cancel checks inside the Word/Origin export loop between samples and graph embedding batches,
- keep the existing in-process export path available until the subprocess path has been verified with real embedded Origin OLE objects.
