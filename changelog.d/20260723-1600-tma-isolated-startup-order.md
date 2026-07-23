2026-07-23 (UTC)

- Make isolated TMA startup complete prior-run validation and child-owned
  hardware preflight before requesting the mounted starting length.
- Keep the recipe stopped when preflight fails or the operator cancels the
  length request.
- Preserve integer and decimal Qt spin-box types while reconstructing recipe
  configuration in the dedicated process.
- Show and log the child process hardware-readiness result before requesting
  the mounted starting length.
- Defer isolated startup until the completed-run history index is ready, then
  log the exact sample identity, indexed record count, and matching metadata or
  builder-project source before any child hardware preflight.
