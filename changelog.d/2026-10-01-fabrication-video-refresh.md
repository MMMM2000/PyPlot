Fabrication relevance now requires a matching composition instead of accepting
unrelated workbooks with the same draw/piece number. Video processing uses the
same measured-sample filter as discovery. Automated Fabrication/Video refreshes
restore saved current-annealing and microscopy dependencies, and reference-only
video refreshes retain saved measurements and historical source references.

Electrical resistance/current sessions from the current-program logger now use
an explicit composition and draw/piece in their run-folder name. Unidentified
sessions stay unassigned, and supply current limits are not treatment setpoints.

Skipped-source reporting resolves each candidate once, avoiding repeated cloud
filesystem calls for every retained curve. Additive annealing imports retain
saved manual phase points from groups outside the imported subset.

Successful Builder updates print ASCII-safe JSON to legacy Windows consoles;
Unicode source paths remain intact in the UTF-8 manifest instead of producing a
false failure after the project has already been saved or promoted.

Equivalent accepted-auto/manual-adjusted annealing decisions with the same
transition values no longer create false sidecar conflicts. Reopening unchanged
annealing/TMA conflicts preserves the original project decision without nesting
it repeatedly inside new history wrappers.
