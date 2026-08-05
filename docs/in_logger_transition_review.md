# Post-run transition review in measurement loggers

## Recommendation

Current annealing and TMA loggers should eventually offer transition review immediately after a run reaches its finished state. The review result should be stored as a small, safe JSON sidecar beside the measurement data. Builder should import that sidecar as the portable review record instead of requiring the `.pydpj` project to be the only authority.

This keeps the fast experimental workflow Martin wants while preserving Builder as the place for cross-sample overview, conflict review, column selection, and public database export.

## Safety and process boundary

- Transition review starts only after acquisition is finished or explicitly stopped and the logger has confirmed its safe final output state.
- Parsing/estimation and the review dialog run outside the hardware-control loop. A slow fit or a closed dialog must never delay safety cleanup.
- Ordinary loading executes JSON only. No pickle or arbitrary object deserialization is introduced.
- Saving uses an atomic temporary-file replacement and never rewrites the raw measurement file.

## Proposed sidecar

Use one `transition_review.json` in a TMA run folder, or a uniquely named `*.transition-review.json` beside a current-annealing file. A shared schema should contain:

- schema version and experiment family (`current_annealing` or `tma`);
- normalized sample identity plus the original source label;
- source-relative path, content fingerprint, and analysis algorithm version;
- target identity (current-annealing graph, or TMA run plus stress/load target);
- review status: accepted automatic, manually adjusted, no transition, excluded, or unreviewed;
- automatic, manual, and final As/Af/Ms/Mf values with units;
- explicitly cleared partial labels, kept separate from whole-target `No transition`;
- review timestamp and optional note.

The content fingerprint is the durable identity. Paths are useful provenance but must not be the sole key because folders can be moved or renamed.

## Builder import and conflicts

1. Builder discovers sidecars while scanning the corresponding measurement section.
2. A sidecar whose fingerprint matches exactly becomes the preferred portable review.
3. Existing project-only reviews remain supported for old data.
4. If both locations contain different explicit decisions, Builder retains both, shows a conflict, and asks which version to adopt; it does not silently overwrite reviewed work.
5. Assemble consumes only the resolved review. Explicit no-transition decisions remain visible and excluded records remain auditable.

## Suggested delivery sequence

1. Extract the existing CA/TMA review record format and identity/fingerprint logic into a shared, UI-independent module.
2. Add sidecar read/write/validation tests, including atomic-save failure and moved-folder matching.
3. Teach Builder to import sidecars while preserving all existing `.pydpj` reviews.
4. Add a post-run review action to the current-annealing logger using recorded test data only.
5. Add the equivalent per-target TMA review after safe run completion.
6. Only after conflict and round-trip tests pass, make immediate post-run review the recommended workflow.

VSM can use the same schema later, but it should not delay the CA/TMA path.
