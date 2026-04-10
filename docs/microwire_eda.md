# Microwire EDA Overview

Microwire EDA is the exploratory analysis layer for assembled Microwire Data Builder datasets. Its job is not just to list correlations, but to help us reason toward the best possible microwire recipe while staying honest about sparsity, confounding, and repeated measurements.

The practical research goal is:

- identify controllable fabrication and annealing levers that move geometry and mechanical performance in a favorable direction
- separate likely causal process signals from derived or selection-driven signals
- compare composition-family effects against within-family process effects
- rank the next most informative experiments for improving stress, strain, fracture stress, and fracture strain
- avoid over-claiming from duplicated tests, derived current-density terms, or heavily imbalanced composition coverage

The workflow is analysis-first:

- prefer measured strain and fracture endpoints over legacy broke/OK labels
- surface data sufficiency and missingness early
- separate process-side signals from geometry-side signals
- treat the project `.pydpj` as the preferred source of truth when available
- compare raw measurement rows against per-wire aggregated views before calling a trend robust
- compare cross-composition trends against composition-specific signals when enough rows exist
- produce machine-readable findings so an agent can inspect, summarize, and iterate autonomously

## Inputs

- Builder project: `.pydpj`
- Assembled export: `.xlsx`, `.xls`, `.xlsm`, `.csv`
- Builder-launched runs can also pass an in-memory Assemble dataframe directly

Preferred order of trust:

1. copied `.pydpj` project file
2. transient Assemble rebuild from the copied `.pydpj` when saved Assemble rows are stale or missing
3. direct Assemble dataframe supplied by the Builder
4. exported spreadsheet only when the project is unavailable

The `.pydpj` should be preferred because it preserves both the saved Assemble rows and the section payloads needed for a rebuild when the Assemble cache is incomplete.

Default real project for local development:

- `/Users/martin/Library/CloudStorage/GoogleDrive-elias@rvmagnetics.com/My Drive/1 Projects/Praha/microwire_project.pydpj`

Reference PDF used for the original analysis framing:

- `/Users/martin/Library/CloudStorage/GoogleDrive-elias@rvmagnetics.com/My Drive/1 Projects/Praha/RF_EDA.pdf`

## Copy-Safe Rule

Never run verification or ad hoc analysis directly against the user's real Praha project file.

- For CLI and agent-driven `.pydpj` analysis, Microwire EDA now makes a disposable copied project by default.
- If saved Assemble rows are missing, or if `--microwire-eda-force-project-rebuild` is used, Microwire EDA rebuilds the assembled dataframe transiently from the Builder project sections.
- Builder-launched analysis that passes the current Assemble dataframe directly stays read-only and does not mutate the project.
- Tests should always create their own temporary `.pydpj` copies.

This keeps the source project untouched while still letting us use the real assembled dataset as the analysis baseline.

## Current Analysis Model

Primary endpoints:

- `Strain (%)` / `strain_abs`
- `Fracture strain (%)` / `fracture_strain_abs`
- `Stress (MPa)`
- `Fracture stress (MPa)`

Auxiliary legacy context:

- `Brittle`
- `Broke`
- derived `is_broken`

Important derived-context metrics:

- current-density terms are useful, but are not fully independent variables because they are calculated from current and cross-sectional area
- geometry-derived terms such as `d/D`, coating thickness, and glass fraction should be interpreted as proxies for internal stress state and glass constraint, not as direct causal proof on their own
- composition should be viewed both as the nominal string label and, when possible, as parsed elemental content (`Ni`, `Fe`, `Ga`, `Co`, `Cu`, `Si`, `Sn`, `Mn`) plus additive-family indicators

## Repeated Measurements

Repeated measurements on the same `Composition + Microwire` key are expected and should not be treated as automatically independent evidence.

Microwire EDA should reason about repeated tests in at least three complementary views:

- `raw measurement rows`
  Keep every measured row. Useful for seeing the full spread and for identifying unstable wires, but can overweight heavily repeated samples.
- `per-wire median`
  Collapse repeated rows to a representative central value. Useful for a conservative "typical wire" view.
- `per-wire best`
  Collapse repeated rows to the best observed normal or fracture outcome for that wire. Useful when the research question is "what is this recipe capable of when it works well?"

The raw view answers reproducibility and dispersion questions. The median view answers typical-performance questions. The best view answers capability questions. A signal should be treated as robust only if its direction is reasonably stable across at least the raw and median views, and preferably also explainable physically.

If a trend appears only in the best-per-wire view, it should be labeled as capability-sensitive rather than robust.

Rows missing a complete `Composition + Microwire` key should not be silently dropped in aggregated modes. Preserve them as single-measurement rows so data sparsity is explicit rather than hidden by grouping.

Legacy breakage is still useful for backward compatibility, but it is no longer the center of the report because newer measurements contain real normal-strain and fracture-strain/stress values.

## Report Sections

The current report is structured around exploratory questions:

- Data quality
  Coverage, endpoint availability, duplicate-wire checks, and row-scope information.
- Endpoint overview
  Distributions of strain, fracture strain, stress, and fracture stress.
- Process to outcome
  Correlations between controllable fabrication/annealing parameters and the measured endpoints.
- Geometry to outcome
  Correlations and scatter views for `d`, `D`, and `d/D`.
- Cohort splits
  Cross-composition coverage plus per-composition top process signals for each endpoint when enough rows exist.
- Interaction views
  Pairplot and parallel-coordinate views for the overlapping numeric subsets.
- Time drift
  Production-date trends and month-level coverage summaries.
- Findings
  Auto-generated observations plus explicit cautions about sparsity and confounding.

The report should increasingly be organized around decision-useful questions rather than generic EDA sections alone:

- which controllable fabrication variables most consistently shape `d`, `D`, `d/D`, coating thickness, and glass fraction?
- which geometry signatures co-occur with better standard and fracture performance?
- do current or current-density terms add information beyond what is already implied by diameter?
- do composition families shift the operating window, or do process parameters dominate within a family?
- which current winners point to the most promising next draws, sibling wires, or compositions to test?

## Autonomous Outputs

Microwire EDA writes a bundle that is meant to be consumed by both humans and agents:

- `microwire_eda_report.html`
- `microwire_eda_summary.xlsx`
- `microwire_eda_dataset.csv`
- `microwire_eda_findings.json`
- `microwire_eda_findings.md`
- `microwire_eda_manifest.json`
- optional `microwire_eda_figures.pdf`

The findings JSON is the best machine-readable summary for autonomous follow-up. It includes:

- row counts
- sufficiency summary
- copied-project path used for the run
- structured findings with headline, detail, evidence, and confidence

## Python API

Key public entry points in `microwire_eda`:

- `MicrowireEdaConfig`
- `run_analysis(config, progress_callback=None)`
- `write_analysis_artifacts(analysis, progress_callback=None)`
- `generate_report(config, progress_callback=None)`

Compatibility helpers still used by tests and UI:

- `detect_input_kind(...)`
- `load_analysis_frame(...)`
- `load_input_frame(...)`
- `canonicalise_frame(...)`
- `apply_row_scope(...)`

## CLI Usage

Basic run:

```bash
/Users/martin/PyPlot/.venv/bin/python launcher.py \
  --microwire-eda "/path/to/project.pydpj" \
  --out "/tmp/microwire-eda" \
  --microwire-eda-title "Microwire EDA Report"
```

Explicit disposable copy location:

```bash
/Users/martin/PyPlot/.venv/bin/python launcher.py \
  --microwire-eda "/path/to/project.pydpj" \
  --out "/tmp/microwire-eda" \
  --microwire-eda-working-copy-dir "/tmp/microwire-eda/work"
```

Useful flags:

- `--rows all|filtered|selected`
- `--no-microwire-eda-copy-project`
- `--microwire-eda-force-project-rebuild`
- `--microwire-eda-no-legacy-breakage`
- `--microwire-eda-no-composition-splits`
- `--microwire-eda-no-findings`

The CLI prints the key artifact paths and the first few findings so an agent can run it non-interactively.

## RF_EDA Reference Alignment

`RF_EDA.pdf` is still useful as the historical baseline for the original microwire analysis concept. From the first pages and section outline, the original framing emphasized:

- dataset overview and completeness
- strain distributions
- stress analysis
- composition analysis
- process parameter vs strain
- parameter interactions
- broke vs non-broke comparison
- symbolic regression / recommendations

What stays useful:

- completeness/coverage framing
- distribution views
- composition splits
- process-vs-endpoint trend hunting
- interaction views

What is now outdated or demoted:

- broke vs non-broke as the primary success target
- recommendation language that assumes the older label set is sufficient on its own

Modern replacement:

- analyze normal strain and fracture strain/stress directly whenever measured
- keep broke/brittle labels only as auxiliary context
- state clearly when endpoint coverage is too sparse for strong claims
- explicitly compare raw repeated-row evidence against per-wire aggregated evidence
- use the report to support experiment selection for "best possible microwire" rather than only post-hoc description

## Interpretation Cautions

- Correlations here are observational, not causal proof.
- Sparse endpoint coverage can make rankings unstable.
- Composition imbalance can dominate apparent global signals.
- Geometry may reflect both production settings and post-production screening, so treat geometry-side findings differently from controllable process findings.
- Current density is partly algebraic because it depends on current and measured diameter.
- Legacy stress can also be partly formula-driven when it is back-calculated from diameter and load/mass fields.
- Repeated measurements on one wire can inflate confidence if raw rows are treated as fully independent.
- Best-per-wire trends and median-per-wire trends answer different scientific questions; both should be inspected before declaring a design rule.

The next stage, once this exploratory layer is trustworthy, is to decide what "best possible microwire" should mean and then add recommendation logic on top of the findings rather than forcing an objective too early.
