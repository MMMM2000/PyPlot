# Physical sample identity: staged implementation

## Meaning

A physical piece has composition + source wire + sN, independent of its run,
instrument or treatment. Remeasuring the same piece keeps its identifier.
New pieces use positive sN. Old labels are retained, never retrospectively
assigned guessed provenance. Numeric filename wire tokens (12_2) and display
tokens (12/2) share a key; other composition/wire aliases are not guessed.

## Storage investigation and safe first stage

Existing naming history is per-logger QSettings. Builder project files support
local exclusive save locks, but a lock file in a cloud-synchronised folder does
not establish a distributed allocation authority. No central allocator has been
verified. Do not use a shared Drive SQLite database or a synchronised counter.

This stage uses one workstation-local SQLite registry for the general logger,
manual stress-strain logger and TMA sample fields. Opening the chooser does not
allocate a piece. The operator explicitly selects an existing piece or records
a new one. The chooser clearly states its local-only coverage. Manual edits and
existing saved settings remain supported; they do not silently enter the registry.
Records are NOT an inventory of all previous experiments. Check other computers
and physical labels before assigning a new label.

The registry lives under Qt GenericDataLocation/PyPlot/physical-samples. It stores
an immutable UUID, composition, wire, label and provenance. Its API supports
parent-piece lineage, atomic imports and offline collisions. Independent machines
can allocate the same sN; merging retains both claims, blocks ambiguous selection,
and never silently renumbers historical records. Same-workstation concurrent
creation is serialised. There is deliberately no delete/recycle API.

The chooser only places the selected readable label into existing filename and
metadata fields. UUID export and lookup across downstream systems are NOT yet
implemented. Neither this registry nor its dialog is proof of global uniqueness.

## Remaining rollout (not delivered by this stage)

- Current annealing's custom spinbox needs explicit change notification and bounds
  tests before adapting the chooser. AC susceptibility and flexible acquisition
  tools need naming/sidecar audit; microscope currently groups composition+wire.
- Persist physical UUID and provenance through run sidecars, Builder/EDA imports,
  exports and cross-instrument joins. Keep wire-level defaults separate from
  piece-specific diameter and treatment metadata.
- Add reviewed import/export UI and collision resolution with provenance; the
  foundational merge API does not automatically scan legacy research files.
- Choose a real shared allocation authority or a documented single allocation
  owner before claiming globally sequential IDs. Offline allocations remain
  provisional until checked; unknown historical labels remain unknown.
- Operator Windows acceptance and visual review are still required. No hardware
  tests, real project rewrites, merge or deployment are part of this stage.

## UI intent

Use existing Qt naming controls with one small Samples action. A plain dialog
shows composition/wire, local coverage warning, recorded pieces and explicit
existing/new choices. No decorative motion or instrumentation changes.
