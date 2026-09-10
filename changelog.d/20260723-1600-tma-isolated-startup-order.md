2026-07-23 (UTC)

- Make isolated TMA startup complete prior-run validation and visible-UI
  hardware preflight before requesting the mounted starting length.
- Keep the recipe stopped when preflight fails or the operator cancels the
  length request.
- Preserve integer and decimal Qt spin-box types while reconstructing recipe
  configuration in the dedicated process.
- Recheck hardware in the child after the UI releases every device handle and
  process lease, and before authoritative recipe timing begins.
- Defer isolated startup until the completed-run history index is ready, then
  log the exact sample identity, indexed record count, and matching metadata or
  builder-project source before any hardware preflight.
- Restore explicitly selected scale and PSU serial ports in the child even
  when its asynchronous port enumeration has not populated those choices yet,
  and include the recent child log in hardware-preflight faults.
