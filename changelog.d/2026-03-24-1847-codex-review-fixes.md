2026-03-24 18:47

- Fixed Microwire EDA canonicalization so duplicate alias columns are merged before downstream analysis, preventing suffixed duplicate fields from being ignored.
- Fixed Microwire EDA report generation to honor `export_png_bundle=False` while still producing HTML and optional PDF figure output.
- Fixed video review override tracking so propagated draw-length edits record sibling history, show overwrite highlighting/tooltips, and support restoring prior values.
- Fixed video review completion flow so blank `Notes` no longer blocks completion or keyboard advance.
- Fixed annealing graph migration to preserve both legacy non-1000 graph columns when upgrading saved data.
- Fixed annealing export handling so single follow-up graph assets are stored/exported as scalars and single-item legacy lists still embed in Excel.
