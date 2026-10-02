# Fixed

- Keep completed TMA reviews stable while packaged measurement data loads lazily. Empty preview placeholders no longer replace or contradict measured-content identities, and saved unmatched history remains quarantined until a unique loaded-content match is available. Portable review values, cleared thresholds and source fingerprints are preserved.

- Keep saved TMA decisions for replaced raw data separate from older embedded curves until their normalized measurement fingerprints agree. Preserve both source revisions and allow uniquely matching loaded data to restore the appropriate decision.
