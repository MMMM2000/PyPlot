# TMA Remote Diagnostics

`data_logging.mini_dma_logger.tma_diagnostics` creates read-only diagnostic
summaries and bundles from a TMA run folder. It is a pure Python command-line
path: it imports no Qt UI, opens no modal dialogs, and connects to no hardware.

## Finished-run bundle

Run the command after the logger has completed final metadata writes:

```powershell
uv run python -m data_logging.mini_dma_logger.tma_diagnostics bundle <run-folder>
```

By default, output is written beside the run as
`<run-folder-name>_diagnostics/`. Use `--output-dir <folder>` to select another
location. The tool refuses to put outputs inside the source run folder and does
not edit, rename, or normalize any source run file.

The output directory contains:

- `diagnostic_summary.json`, the stable machine-readable summary;
- `diagnostic_summary.md`, a compact remote-review overview;
- `diagnostic_bundle.zip`, a deterministic ZIP containing both summaries and
  every available discovered run artifact.

The ZIP has sorted entries and fixed entry timestamps. Repeating the command
against unchanged inputs produces the same ZIP bytes.

## Discovery and finalization

Final `metadata.json` is authoritative. Artifact discovery first uses paths in
its `logging` object, including `measurement_csv`, `control_trace_csv`,
`raw_scale_sidecar`, `ui_telemetry_csv`, and `run_log_txt`. Legacy fixed names
such as `measurement.csv` and `scale_raw.csv` are used only when the matching
final logging path is absent. Resolved paths must remain within the run folder.

Normal summary and bundle commands require `session_state: finished`, a
`finished_utc` value, completed source-control capture, and no sensor sidecar
with active or pending reconciliation. Finalized logging failures remain
visible diagnostic outcomes; they are not hidden or rewritten.

For an explicitly point-in-time capture of an active run or metadata whose
asynchronous patches are still pending, add `--snapshot`:

```powershell
uv run python -m data_logging.mini_dma_logger.tma_diagnostics bundle <run-folder> --snapshot
```

Snapshot mode is marked in both summaries. It should not be presented as a
finished-run bundle.

## Summary contents

The summary covers:

- measurement, control, raw-scale, and UI row counts and UTC ranges;
- cross-file offsets from run start, elapsed/UTC span agreement, interval
  percentiles, maximum gaps, and gap counts;
- control decisions, results, reasons, command-bearing rows, and the final
  control trace row;
- the explicit stop reason, category, and detail;
- recorded source-control branch, commit, dirty state, and capture outcome;
- the complete recorded scale profile/settings, control timing settings, and
  control-logic fingerprint, including the frozen `prague_legacy` or
  `kosice_adaptive` force-control profile;
- final run-log completion and asynchronous sensor-sidecar reconciliation
  outcomes;
- availability, size, relative path, and SHA-256 digest for every discovered
  source file.

Missing optional sidecars such as passive IR temperature or setup data are
reported as unavailable and do not prevent a finished-run bundle.

## Historical identity correction

For a historical transcription correction, write an audited sidecar outside
the run folder:

```powershell
uv run python -m data_logging.mini_dma_logger.tma_diagnostics correct-identity <run-folder> `
  --set microwire=10/4 `
  --reason "Corrected from operator notebook" `
  --operator "Operator name"
```

`identity_correction.json` keeps the original and effective identity, an
append-only correction history, the final metadata path and SHA-256 digest,
and SHA-256 digests for all available source artifacts. A later correction is
refused if the final metadata hash no longer matches the existing correction
sidecar. Re-run `bundle` to include the correction sidecar in the ZIP.

To write only JSON and Markdown summaries without a ZIP, use `summarize` with
the same `--output-dir` and `--snapshot` options.
