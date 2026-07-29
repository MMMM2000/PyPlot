# Post-run transition review in measurement loggers

## Implemented workflow

Current annealing and TMA loggers offer transition review immediately after a run reaches its finished state. The review result should be stored as a small, safe JSON sidecar beside the measurement data. Builder should import that sidecar as the portable review record instead of requiring the `.pydpj` project to be the only authority.

The TMA Dashboard header also has a permanent `Review transitions...` split button. Its main action opens the latest completed run; the arrow menu selects an older run folder. The logger refuses to review the actively acquiring run. The automatic post-run prompt remains available after successful recipe completion.

The Current Annealing Dashboard header has the same `Review transitions...` split button beside `Configure plots`. Its main action opens the latest completed measurement; the arrow menu selects another run folder or a legacy standalone measurement file. The logger also refuses to review an actively acquiring run.

This keeps the fast experimental workflow Martin wants while preserving Builder as the place for cross-sample overview, conflict review, column selection, and public database export.

## Review interaction

The logger-facing editor is decision-first rather than schema-first:

- **Accept automatic** saves the automatic transition points as reviewed.
- **Adjust manually** shows one compact table of the relevant points. Select a row and click the graph, or type the chosen value. Saving stays disabled until at least one point is adjusted or omitted.
- **No transition** records a reviewed categorical result with no final numeric transition values.
- **Exclude this target from Builder analysis** is a secondary option. It retains the reviewed values in the sidecar while setting `analysis_included: false`.

Only targets present in the run are shown. Current Annealing hides the target list because it has one graph; multi-stress TMA runs retain a short target list with human labels such as `100 MPa · 2.92 g`. A single **Omit selected point** action replaces the former per-label clear-checkbox grid.

## Safety and process boundary

- Transition review starts only after acquisition is finished or explicitly stopped and the logger has confirmed its safe final output state.
- Parsing/estimation and the review dialog run outside the hardware-control loop. A slow fit or a closed dialog must never delay safety cleanup.
- Ordinary loading executes JSON only. No pickle or arbitrary object deserialization is introduced.
- Saving uses an atomic temporary-file replacement and never rewrites the raw measurement file.

## Portable sidecar

New current-annealing runs and TMA runs use one `transition_review.json` in the run folder. Legacy flat current-annealing files remain supported through a uniquely named `<measurement>.transition-review.json` beside the data file. A shared schema should contain:

- schema version and experiment family (`current_annealing` or `tma`);
- normalized sample identity plus the original source label;
- source-relative path, content fingerprint, and analysis algorithm version;
- target identity (current-annealing graph, or TMA run plus stress/load target);
- review status: accepted automatic, manually adjusted, no transition, excluded, or unreviewed;
- automatic, manual, and final As/Af/Ms/Mf values with units;
- explicitly cleared partial labels, kept separate from whole-target `No transition`;
- review timestamp and optional note.

The content fingerprint is the durable identity. Paths are useful provenance but must not be the sole key because folders can be moved or renamed.

`No transition` and `Excluded` have intentionally different meanings. `No transition` records a useful reviewed experimental outcome: no transition was observed. Its numeric final transition values are empty (`included: false`), but `analysis_included: true` keeps the categorical result in Builder/Assemble analysis, including reviewed coverage and no-transition counts. `Excluded` is an analysis decision: automatic and manually reviewed transition values remain stored for traceability, while both `included: false` and `analysis_included: false` prevent the target from contributing to numeric or categorical analysis.

## Builder import and conflicts

1. Builder discovers sidecars while scanning the corresponding measurement section.
2. A sidecar whose fingerprint matches exactly becomes the preferred portable review.
3. Existing project-only reviews remain supported for old data.
4. If both locations contain different explicit decisions, Builder retains both, shows a conflict, and asks which version to adopt; it does not silently overwrite reviewed work.
5. Assemble consumes only the resolved review. Explicit no-transition decisions remain visible and excluded records remain auditable.

## Retrospective review

The initial historical campaign is intentionally limited to Prague measurement roots. Do not inventory or write Košice data in this pass; Košice can be handled later as a separately reviewed campaign.

Portable reviews can be created from existing Builder project decisions without modifying the project or raw measurements:

```powershell
uv run python scripts/backfill_transition_reviews.py --project <copy.pydpj> --root <data-root> --out artifacts/transition-review-backfill
```

The command is a dry run by default. Add `--write` only after reviewing the audit summary. Every candidate, including an absolute path saved in the project, must resolve inside one of the explicit `--root` directories. An out-of-root path is reported and never read or written. The command writes a sidecar only for an exact or unique in-root measurement match, never overwrites a conflicting sidecar, and records every written, skipped, ambiguous, stale, or conflicting item in a JSON audit manifest. Repeated TMA sweeps at the same stress are represented by one stress target using the final sweep, with the sweep count retained in target metadata.

Historical Current Annealing reviews that contain manual final values but were stored as `accepted_auto` are normalized to `manual_adjusted` during backfill. This repairs the review-state label without changing the reviewed transition values.

For runs that were never reviewed in Builder, load the legacy file or new run folder in the Current Annealing PyPlot plugin and use `Review loaded transitions...`. Saving creates `transition_review.json` inside a new-style run folder or `<stem>.transition-review.json` beside a legacy flat file. The stored values are transition currents in mA; converting them to transition temperatures requires a separate calibrated current-to-temperature relationship.

VSM can use the same schema later, but it does not block the CA/TMA path.
