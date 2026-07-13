# TMA Remote Diagnostics And Sample Identity

## Run identity preflight

TMA freezes one canonical sample identity before it creates a run folder. The snapshot contains:

- composition, microwire, specimen, condition, sample name, and derived sample ID;
- diameter and its Builder-project, fabrication-record, or manual/saved-setting provenance;
- the configured preflight length and a note that mandatory setup may derive the runtime `l0`;
- the final run-folder/base filename, including an automatically selected `_runNN` suffix.

The same snapshot drives the run-folder identity, measurement/setup headers, Recipe-tab sample display, and backward-compatible identity fields in `metadata.json`. Runtime setup length and its measured-length/position provenance are recorded separately. Editing widgets after a run starts does not rewrite the frozen identity.

Preflight rejects a run before folder creation when structured composition or microwire fields disagree with the sample name or output filename. Fix the visible fields and start again; do not rename or edit an old raw run to make it match.

## Historical identity corrections

Use an append-only correction sidecar for historical transcription fixes. This command does not modify `metadata.json` or measurement files:

```powershell
uv run python -m data_logging.mini_dma_logger.tma_diagnostics correct-identity <run-folder> `
  --set microwire=10/4 `
  --reason "Corrected from operator notebook" `
  --operator "Operator name"
```

The resulting `identity_correction.json` preserves the original identity, effective corrected identity, correction values, reason, operator, UTC timestamp, and the raw metadata SHA-256 digest. Repeated corrections append to the same audit history.

## Diagnostic summaries and bundles

Create a machine-readable summary:

```powershell
uv run python -m data_logging.mini_dma_logger.tma_diagnostics summarize <run-folder>
```

Create the summary plus a compact ZIP suitable for remote review:

```powershell
uv run python -m data_logging.mini_dma_logger.tma_diagnostics bundle <run-folder>
```

`diagnostic_summary.json` reports the frozen/effective identity, explicit scale profile, accepted/rejected sample counts, scale interval percentiles, control decisions/results/gate reasons, linked motor-command count, UI timing/stall and dropped-row counters, stop/fault context, and source/control fingerprints. `diagnostic_bundle.zip` includes the summary and available metadata, trace, telemetry, raw-scale, run-log, and correction sidecars. It intentionally omits the usually larger measurement payload.

## Logged observability

`metadata.json` includes the frozen `sample_identity`, runtime length provenance, scale profile and sample counters, interval percentiles, UI/logging counters, controller state, last motor-command linkage, stop context, and source-control fingerprint.

`control_trace.csv` adds the scale profile and counts, controller state and gate reason, estimator window/sample count/noise/slope/freshness, motor command ID/issue time/target, and post-move response sample linkage. These fields observe existing behavior; they do not change control decisions, motion correction sizes, timing, or safety policy.
