2026-07-15 13:00

- Keep Builder projects as one portable `.pydpj` file while moving new saves to a strict version 3 ZIP64 package with independently checksummed, lazily loaded sections and content-addressed binary data.
- Load project section tables in staged background work and defer large measurement payloads until their feature is opened, reducing startup and project-open stalls without losing reviewed or explicit no-transition states.
- Add explicit, copy-only migration of trusted legacy v1/v2 projects into a distinct v3 output, with isolated pickle handling, progress, cancellation boundaries, and bounded payload sizes.
- Teach Builder automation, Assemble export, Microwire EDA, and Word report paths to consume the packaged project format while preserving saved public column visibility/order and existing analysis semantics.
- Keep packaged-project graph cells responsive with explicit lazy-preview placeholders; transition views load only their active CA, VSM, or TMA dependency while preserving every saved review state.
