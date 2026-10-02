# Fixed

- Keep completed TMA reviews stable while packaged measurement data loads lazily. Empty preview placeholders no longer replace or contradict measured-content identities, and saved unmatched history remains quarantined until a unique loaded-content match is available. Portable review values, cleared thresholds and source fingerprints are preserved.

- Keep saved TMA decisions for replaced raw data separate from older embedded curves until their normalized measurement fingerprints agree. Preserve both source revisions and allow uniquely matching loaded data to restore the appropriate decision.

- Consolidate repeated pending saves of the same TMA decision to the latest acknowledged revision. Preserve changed older decisions as superseded history, including delayed acknowledgements, while retaining same-revision disagreements as unresolved conflicts.

- Keep reviews for known lazy sources attached to their own source while preview batches load. A matching copied run no longer receives another source's pending decision; verified source previews override stale placeholders, while genuinely moved sources retain unique-content recovery.
