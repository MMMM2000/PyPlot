# Current Annealing Plan

This note captures the agreed direction for a later implementation pass in a separate worktree.

## Goal

Keep the current annealing workflow simple for the main builder table:

- one dedicated `1000 mA` column per microwire
- one aggregated column for all other annealing measurements for that same microwire

This should support the real data shape now appearing in Praha:

- one anchor high-current measurement per sample
- zero or more additional measurements at other conditions

## Agreed Product Shape

For each microwire row, the current annealing section should treat measurements as:

- `1000 mA`: the preferred anchor measurement
- `Other annealing`: every non-anchor measurement, regardless of whether it is lower current, a different condition, or a variant

We do not currently need separate first-class columns for:

- `low mA`
- per-condition buckets
- multiple named follow-up categories

The user-facing rule is:

- if an exact `1000 mA` file exists, show it in the dedicated high-current slot
- everything else belongs to the aggregated secondary slot

## Why This Is Enough For Now

The current code already groups multiple annealing files per microwire and can keep extra records. The problem is mainly presentation and exported structure, not basic ingestion.

Today the builder still thinks in terms of:

- `Graph — 1000 mA`
- `Graph — low mA`
- `Graph — other mA`

That is more complicated than needed for the current workflow and does not match the desired mental model.

## Target Behavior

### 1. Grouping

Grouping should stay by microwire key:

- composition
- draw
- piece
- optional suffix

All annealing files that resolve to the same microwire key should stay in one group.

### 2. Anchor Selection

Anchor selection should remain simple and deterministic:

- prefer exact `1000 mA`
- if multiple `1000 mA` files exist, prefer the primary/non-variant file
- if still ambiguous, fall back to stable filename ordering

If a microwire has no exact `1000 mA` file:

- leave the anchor slot empty
- keep all available files in `Other annealing`
- surface a warning in the log, but do not discard the row

### 3. Secondary Measurement Bucket

The aggregated secondary bucket should contain all non-anchor measurements:

- lower-current files
- repeated runs
- alternative variants such as `a`, `s2a`, or condition-tagged filenames
- same-current non-anchor files if they are not the selected `1000 mA` anchor

Ordering inside that bucket should be stable:

- by numeric setpoint when available
- then by filename

### 4. Section Table

Replace the current two-secondary-column model with:

- `Graph — 1000 mA`
- `Graph — other annealing`

The section table should no longer require a dedicated `Graph — low mA` column.

### 5. Preview Behavior

Preview behavior should be:

- single preview card for `1000 mA`
- stacked or tiled previews for `Other annealing`

The aggregated preview should still allow opening an individual underlying record when needed.

### 6. Export / Assemble Behavior

Assemble should preserve:

- the selected `1000 mA` source file
- the list of all non-anchor source files
- the rendered figure(s) for the aggregated bucket if graph export is enabled

The assembled database should not try to flatten all follow-up measurements into many separate columns yet.

Preferred output fields:

- `File 1000 mA`
- `Figure — 1000 mA`
- `Other annealing files`
- `Figure — other annealing`
- `Setpoints (mA)`
- `Sources`

`Setpoints (mA)` and `Sources` should continue to act as the complete audit trail.

## Scope For The Future Implementation

### In Scope

- simplify the current annealing UI model to `1000 mA + other`
- simplify exported/current section columns accordingly
- keep all non-anchor measurements visible and accessible
- keep compatibility with current grouped-record behavior

### Out Of Scope

- redesigning current density calculations
- changing phase-point picking rules
- inventing new condition taxonomies
- forcing one row per annealing condition
- touching the active video review workflow

## Likely Code Areas

The later work will probably touch:

- `microwire_data_builder/core.py`
- `microwire_data_builder/ui.py`
- `docs/database_builder.md`
- regression tests under `tests/regression/`

Main hotspots to revisit:

- high/low selection helpers
- `other` measurement selection
- annealing section table columns
- preview rendering for grouped measurements
- worksheet/export column naming
- assemble integration

## Suggested Implementation Steps

1. Replace the conceptual model of `high + low + other` with `anchor + other`.
2. Rename the UI/export columns to match that simpler model.
3. Update preview rendering and open-graph actions to use the new bucket names.
4. Update assemble/export wiring so no logic still expects `low mA`.
5. Add regression tests for:
   - one `1000 mA` + one other file
   - one `1000 mA` + many other files
   - no `1000 mA` file
   - duplicate `1000 mA` variants
6. Update `docs/database_builder.md` once behavior is actually changed.

## Test Cases To Preserve

Use Praha-style examples when implementing:

- one `1000 mA` and one low-current follow-up
- one `1000 mA` and multiple distinct follow-ups
- one `1000 mA` and multiple same-current condition variants
- multiple `1000 mA` files with one preferred anchor and the rest in `Other annealing`

Concrete examples previously observed in Praha include samples shaped like:

- one `1000 mA` plus `90 mA` and `100 mA`
- one `1000 mA` plus `140 mA` plus multiple `100 mA` condition variants

## Migration Notes

When implementing later, be careful with persisted builder state:

- old projects may still contain `Graph — low mA`
- old exports and hidden-column preferences may reference the previous column names
- any migration should preserve existing payloads where possible and silently map legacy fields forward

## Important Constraint

Do this work only in a separate worktree after the current video-review fixes are safely out of the way.
