# Post-run transition review in measurement loggers

## Implemented workflow

Current annealing and TMA loggers offer transition review immediately after a run reaches its finished state. The review result should be stored as a small, safe JSON sidecar beside the measurement data. Builder should import that sidecar as the portable review record instead of requiring the `.pydpj` project to be the only authority.

The TMA Dashboard header also has a permanent `Review transitions...` split button. Its main action opens the latest completed run; the arrow menu selects one older run or starts a queue from a selected parent folder. The queue includes only that folder and its direct child run folders containing `measurement.csv`. The logger refuses to review while acquisition is active. The automatic post-run prompt remains available after successful recipe completion.

The Current Annealing Dashboard header has the same `Review transitions...` split button beside `Configure plots`. Its main action opens the latest completed measurement; the arrow menu selects another run folder, a legacy standalone measurement file, or a parent-folder queue. The queue includes only that folder and its direct child run folders containing `measurement.txt`. The logger also refuses to review while acquisition is active.

This keeps the fast experimental workflow Martin wants while preserving Builder as the place for cross-sample overview, conflict review, column selection, and public database export.

## Review interaction

The logger-facing editor reviews each transition point directly. Every As, Af, Ms, and Mf row has three exclusive choices:

- **Auto** shows and selects the detector value. It is disabled when the detector returned no value; a missing estimate is not silently treated as a confirmed absence.
- **Manual** selects a graph-picked or directly entered value. One shared numeric field follows the selected row, avoiding a grid of text boxes.
- **Not observed** records that the reviewer intentionally found no usable value for that individual point.

The selected button is the chosen result, so there is no separate status dropdown or chosen-value column. Saving remains disabled until every displayed point at every TMA stress target has an explicit choice, and every selected manual point has a numeric value. Choosing **Not observed** for every point derives the whole-target `no_transition` status; mixed automatic, manual, and not-observed choices derive `manual_adjusted`. **Exclude from Builder analysis** remains an independent secondary decision and retains all reviewed values in the sidecar.

Only targets present in the run are shown. Current Annealing hides the target list because it has one graph; multi-stress TMA runs retain a short target list with human labels such as `100 MPa · 2.92 g`. The compact choice panel sits beside the graph, keeping the plot large without spending a full-width block below it. Per-row decisions map backward-compatibly to `auto_values`, `manual_values`, `final_values`, and `cleared_labels`.

The interactive plot uses PyQtGraph. Current Annealing uses a compact cycle selector so cycles 1, 2, and later cycles are reviewed independently without displaying every cycle row at once. Repeated TMA sweeps at the same stress are separate targets labelled `sweep 1/2`, `sweep 2/2`, and so on. The curve and marker objects are reused while navigating, downsampling and clipping are enabled, and manual markers remain draggable.

For TMA, every chosen As/Af/Ms/Mf current also derives the strain at that point from the displayed trace. As/Af are interpolated on the increasing-current leg and Ms/Mf on the decreasing-current leg; the calculation never crosses branches or extrapolates beyond measured current. It uses the same per-stress-target minimum-length `L0` convention as the existing TMA peak-strain summary. If absolute length cannot be reconstructed because the run lacks an initial length, the existing fallback is recorded explicitly as per-target minimum recorded strain. The selected row shows its derived strain and `L0` when available, and the sidecar stores both the values and reference method. A whole-target `No transition` has no point strains. An `Excluded` target retains them for audit.

A multi-run queue loads one run at a time, shows `run N/total`, changes the action to `Save & next`, and stops without touching later runs when Cancel is pressed. Each completed run is saved atomically before the next run opens.

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
- for TMA, derived strain at each final transition current plus the strain-reference method and `L0` where reconstructable;
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

The expanded TMA workbook export includes `TMA strain at As/Af/Ms/Mf (%)` beside the reviewed transition currents. These are point strains on each target's per-target `L0` reference, distinct from the existing peak-strain column.

## Retrospective review

The initial historical campaign is intentionally limited to Prague measurement roots. Do not inventory or write Košice data in this pass; Košice can be handled later as a separately reviewed campaign.

Portable reviews can be created from existing Builder project decisions without modifying the project or raw measurements:

```powershell
uv run python scripts/backfill_transition_reviews.py --project <copy.pydpj> --root <data-root> --out artifacts/transition-review-backfill
```

When all reviewed measurements are expected to remain at their saved paths, add `--exact-only`. This disables recursive filename fallback, blocks existing out-of-root paths, and makes the audit substantially faster. Omit it only when a separately reviewed moved-file recovery pass is required.

The command is a dry run by default. Add `--write` only after reviewing the audit summary. Every candidate, including an absolute path saved in the project, must resolve inside one of the explicit `--root` directories. An out-of-root path is reported and never read or written. The command writes a sidecar only for an exact or unique in-root measurement match, never overwrites a conflicting sidecar, and records every written, skipped, ambiguous, stale, or conflicting item in a JSON audit manifest. Repeated TMA sweeps at the same stress are preserved as separate targets. Historical `1st:` decisions map to the first sweep; an unqualified legacy stress decision maps to the final sweep for backward compatibility.

Historical Current Annealing reviews that contain manual final values but were stored as `accepted_auto` are normalized to `manual_adjusted` during backfill. This repairs the review-state label without changing the reviewed transition values.

For runs that were never reviewed in Builder, load the legacy file or new run folder in the Current Annealing PyPlot plugin and use `Review loaded transitions...`. Saving creates `transition_review.json` inside a new-style run folder or `<stem>.transition-review.json` beside a legacy flat file. The stored values are transition currents in mA; converting them to transition temperatures requires a separate calibrated current-to-temperature relationship.

VSM can use the same schema later, but it does not block the CA/TMA path.
