# Physical sample identity: staged implementation

## Meaning and current coverage

A physical piece has composition + source wire + sN, independent of its run,
instrument or treatment. Remeasuring the same piece keeps its identifier.
New pieces use positive sN. Legacy labels are retained without guessed identity.
Numeric wire tokens such as 12_2 and 12/2 share a canonical key.

The explicit Samples chooser is integrated with general logging, manual
stress-strain, TMA, current annealing, AC susceptibility, flexible current-program
logging and microscope capture. Opening it does not allocate a piece. The operator
selects an existing record or explicitly records a new one. New forms do not invent
an s1 default; restored legacy labels remain supported and unregistered.

Selected UUID/provenance is exported in TMA, current-annealing, AC, current-program
and microscope metadata. General/manual filenames still use readable labels.
AC resume rejects mismatched piece fields and registered/unregistered UUID mismatches.
Matching labels without UUIDs retain legacy resume behavior; they do not prove identity.
Microscope captures/CSV retain the piece separately from source wire. Piece-specific
diameters and Brittle observations are not written to whole-wire Builder rows.

## Cross-device limitations

Existing histories are per-logger QSettings. Cloud-synced Builder files and lock
files do not establish a distributed allocation authority. No central allocator has
been verified. Do not place this SQLite database in Drive or share a synced counter.

One workstation-local registry lives under Qt GenericDataLocation/PyPlot/physical-samples.
It stores immutable UUID, composition, wire, label and provenance. Local creation is
serialized. There is no delete/recycle API. UI clearly states coverage is local.
Explicit JSON export/import transfers records between computers without overwriting
an existing exchange file. Imports are atomic; conflicting labels retain separate
UUIDs and block ambiguous selection. They are never silently renumbered.

This is not an inventory of all previous experiments or a global uniqueness guarantee.
Check physical labels and other computers before assigning numbers. Manual typing,
restored settings and legacy files do not silently gain a guessed UUID.

## Remaining acceptance and rollout

- Establish a real shared allocation authority or a documented single allocation
  owner before promising globally sequential IDs. Offline allocations are provisional.
- Propagate UUIDs through remaining downstream Builder/EDA imports and joins, and
  implement explicitly reviewed conflict resolution and parent-piece UI if needed.
- Complete operator Windows visual acceptance across all affected logger flows.
- No hardware acceptance, real-project rewrites, merge or deployment is implied.

Use existing Qt naming controls and a small Samples action. The dialog shows
composition/wire, local coverage, known records and explicit existing/new choices.
