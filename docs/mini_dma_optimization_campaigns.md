# Mini DMA Optimization Campaigns

Mini DMA optimization runs must be treated as campaigns, not one-off hardware attempts. The goal is repeatability: a future worker should be able to inspect the campaign folder, branch, manifest, reports, and run artifacts without needing chat memory.

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
3. Start the worker from the latest approved control branch, normally `origin/main` or the current Mini DMA integration branch.
4. Run the campaign checker before any live hardware command:

   ```powershell
   uv run python scripts/mini_dma_campaign_check.py G:/My Drive/1 Projects/Praha/mini DMA/automation_history/campaigns/<campaign>/campaign.yaml
   ```

5. Run only recipes and bench plans referenced by the campaign manifest.
6. After each run, append or regenerate the automation index and produce the standard report:

   ```powershell
   uv run python scripts/mini_dma_report.py G:/My Drive/1 Projects/Praha/mini DMA/automation_history/campaigns/<campaign>/campaign.yaml
   ```

7. The completion handoff must include the campaign path, branch, commit, control logic version/fingerprint, run folders, report paths, metrics, stop reasons, and final HMP output state.

## Campaign Gates

A worker must not start live Mini DMA optimization if any of these are unknown:

- sample composition, microwire, gauge length, and diameter source
- required base branch or integration branch
- actual git branch and commit
- whether the worktree is clean
- control logic version/fingerprint expected by the campaign
- HMP channel ownership and voltage/current limits
- maximum stress and travel safety limits
- report output path

The checker is intentionally conservative. If the manifest does not say what “latest approved control logic” means, the worker should stop and ask the master coordination thread to update the campaign rather than guessing.

## Standard Report Contract

Every campaign report must include the same core plots so results can be compared across days:

- stress versus time
- strain versus measured current
- current-hold regions highlighted
- run summary table with stop reason and branch/commit
- current compliance summary
- stress error RMS, p95, max, and recovery/hold metrics when available

Additional exploratory plots are welcome, but they must not replace the core plot pair.

## Temperature And Ramp-Speed Optimization

For temperature/current-sweep optimization, stages should explicitly list current ramp speeds and dynamic-ramp candidates. A good first campaign structure is:

- baseline fixed ramp at `1.0 mA/s`
- fixed ramp at `0.8 mA/s`
- fixed ramp at `0.6 mA/s`
- optional slower fixed ramp only if precision gain is plausible
- dynamic current-ramp candidate after fixed-speed baselines exist

The dynamic candidate should be judged against the fixed-speed ladder by precision and time, not by whether it merely completes.
