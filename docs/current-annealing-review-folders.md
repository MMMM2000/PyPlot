# Reviewing a Current Annealing collection

Open the arrow beside **Review transitions...** in the Current Annealing logger:

- **Choose run folder...** opens one run containing `measurement.txt`,
  `measurement.csv`, or a local file named by `metadata.json`'s `data_file`.
- **Review runs in parent folder...** includes measurements directly in the
  selected folder and its immediate child folders. It supports legacy TXT/DAT/CSV
  files as well as modern run folders.
- **Review runs including subfolders...** also visits nested groups, such as
  `vacuum test`. Administrative folders (`metadata`, `archive`, `artifacts`,
  `logs`), hidden directories, directory links and run logs are excluded.
- **Choose legacy measurement file...** opens a specific file when its format
  does not match the collection discovery probe.

A modern run contributes its selected measurement once. Collection discovery is
format discovery; opening the review still validates the data and any existing
review's scientific fingerprint. Review runs only after acquisition has stopped.

## Saving and sharing decisions

Save the decision in the review dialog. For `measurement.txt`, the portable review
is `transition_review.json` in the run folder. CSV and legacy files retain their
existing `<file-stem>.transition-review.json` naming. Saving a review does not
modify the raw measurement or automatically publish the Builder database.

After Drive sync finishes, scan/import the collection in Builder. Matching
portable decisions are reconciled with the saved project. Different explicit
project and portable decisions require attention; a mismatched measurement
fingerprint is withheld. Resolve those cases before saving and publishing the
shared database. Coordinate reviewers so two people do not save the same review
file simultaneously.

## Moving legacy measurements

Use the original measurement stem as the new run folder name, with the unchanged
raw data saved as `measurement.txt`. That name carries the current-unit hint for
legacy traces. Keep the associated decision in `transition_review.json` and
verify its normalized fingerprint against the relocated trace before importing.

A collection migration must also preserve raw bytes and existing decisions,
resolve metadata provenance, update Builder references through its normal writer,
and retain a verified recovery copy. Selecting a folder in the review menu does
not perform a migration.
