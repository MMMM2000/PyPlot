# TMA Optimization Campaigns

TMA optimization runs must be treated as campaigns, not one-off hardware attempts. The goal is repeatability: a future worker should be able to inspect the campaign folder, branch, manifest, reports, and run artifacts without needing chat memory.

## Storage Model

Keep raw experiment data and campaign history in the Praha Google Drive:

```text
G:/My Drive/1 Projects/Praha/mini DMA/automation_history/
  README.md
  runs_index.csv
  runs_index.jsonl
  campaigns/
    <date_sample_goal>/
      campaign.yaml
      plans/
      recipes/
      raw_runs/
      reports/
        images/
      analysis/
      handoff.md
  legacy_imports/
  modeling/
```

Keep only reusable tools, templates, schemas, recipes, and report generators in the repository.

## Required Workflow

1. Create or select a campaign folder under `automation_history/campaigns/`.
2. Copy `docs/automation_templates/mini_dma_campaign.yaml` to `campaign.yaml` and fill in the sample, control source, hardware, safety, run stages, and reporting paths.
3. Start the worker from the latest approved control branch, normally `origin/main` or the current TMA integration branch.
4. Run the campaign checker before any live hardware command:

   ```powershell
   uv run python scripts/mini_dma_campaign_check.py G:/My Drive/1 Projects/Praha/mini DMA/automation_history/campaigns/<campaign>/campaign.yaml
   ```

5. Run only recipes and bench plans referenced by the campaign manifest.
6. After each run, append or regenerate the automation index and produce the standard report:

   ```powershell
   uv run python scripts/mini_dma_run_quality.py <run-folder> --write --core-plots
   uv run python scripts/mini_dma_report.py G:/My Drive/1 Projects/Praha/mini DMA/automation_history/campaigns/<campaign>/campaign.yaml
   ```

7. The completion handoff must include the campaign path, branch, commit, control logic version/fingerprint, run folders, report paths, metrics, stop reasons, and final HMP output state.

## Campaign Gates

A worker must not start live TMA optimization if any of these are unknown:

- the optimization objective and success metrics
- sample composition, microwire, gauge length, and diameter source
- required base branch or integration branch
- actual git branch and commit
- whether the worktree is clean
- control logic version/fingerprint expected by the campaign
- HMP channel ownership and voltage/current limits
- maximum stress and travel safety limits
- report output path

The checker is intentionally conservative. If the manifest does not say what “latest approved control logic” means, the worker should stop and ask the master coordination thread to update the campaign rather than guessing.

## Optimization Objective

The control objective must be explicit before a live campaign starts. For current-sweep optimization, the usual goal is:

- minimize stress/load fluctuation during current ramps
- minimize RMS, p95, and maximum stress error
- recover quickly after transformation-driven stress changes
- preserve a clean strain-current curve
- find the useful precision/time tradeoff

The run is not automatically better because it is slower. A `0.2 mA/s` ramp may be useful for a reference curve, or it may waste time if `0.6 mA/s` gives essentially the same stress stability and curve quality. The report should make that tradeoff visible instead of relying on impressions.

Dynamic current-ramp control should be judged by the same standard: it should approach the precision of slower fixed ramps while keeping measurement time closer to faster fixed ramps.

## Generalization Rule

Do not optimize permanent control logic only for one sample, one length, or one composition. Different microwires can have different diameter, gauge length, stiffness, resistance, transformation behavior, and current compliance. Control changes should therefore be based on measured or declared quantities such as:

- diameter and gauge length
- load-path stiffness or calibration results
- noise floor and scale cadence
- motor step size and backlash
- live stress/load error and trend
- current-ramp rate and measured compliance

Hard-coded MPa, mm, mA, or time constants are acceptable only when they are:

- safety guardrails,
- derived from sample geometry or calibration,
- declared in the campaign manifest for that campaign only, or
- temporary experimental probes that are not promoted into permanent control logic without generalization.

If a worker proposes a fixed cap or magic value, it must explain why a physically derived or adaptive rule is not sufficient.

## Standard Report Contract

Every campaign report must include the same core plots so results can be compared across days:

- stress versus time
- strain versus measured current
- current-hold regions highlighted
- run summary table with stop reason and branch/commit
- current compliance summary
- stress error RMS, p95, max, and recovery/hold metrics when available

Additional exploratory plots are welcome, but they must not replace the core plot pair.

## Normal Runs As Evidence

Campaign analysis should include normal non-optimization runs as reference evidence when they are relevant to the same control logic or sample family. This prevents good daytime measurements from being ignored just because they were not created by a bench-plan worker.

Each run should be analyzed once with:

```powershell
uv run python scripts/mini_dma_run_quality.py <run-folder> --write --core-plots
```

This writes `run_quality.json` next to `metadata.json` and creates the standard per-run PNG/JSON summary under `diagnostics/core_plots/`. The raw CSV files remain the source of truth; these outputs are derived caches that can be regenerated when the analyzer improves.
When this command is run on a parent folder, setup-only or otherwise incomplete
run folders still get `run_quality.json`; core-plot failures are reported per
run as `plot_error=...` and do not prevent plots from being generated for the
other runs in the batch.

The quality summary records:

- run type: `optimization_probe`, `validation`, `normal_measurement`, `failed_setup`, or `excluded`
- inclusion status and exclusion reasons
- sample, wire, length, diameter
- control logic version/fingerprint and git branch/commit
- measurement rows and estimated current-loop count
- stress error mean, median, RMS, p95, and max
- current-hold time and fraction
- current compliance ratio
- biggest problem tags

Do not blindly include every run. Early bring-up attempts, setup failures, wire breaks, short runs, and runs with too few current loops should be classified and shown as excluded with reasons. The indexer reads cached `run_quality.json` when present and includes these fields in `runs_index.csv/jsonl`.

## Temperature And Ramp-Speed Optimization

For temperature/current-sweep optimization, stages should explicitly list current ramp speeds and dynamic-ramp candidates. A good first campaign structure is:

- baseline fixed ramp at `1.0 mA/s`
- fixed ramp at `0.8 mA/s`
- fixed ramp at `0.6 mA/s`
- optional slower fixed ramp only if precision gain is plausible
- dynamic current-ramp candidate after fixed-speed baselines exist

The dynamic candidate should be judged against the fixed-speed ladder by precision and time, not by whether it merely completes.
