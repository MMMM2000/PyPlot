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
- Extend additive provenance to any remaining measurement-family import paths;
  implement explicitly reviewed conflict resolution and parent-piece UI if needed.
- Complete operator Windows visual acceptance across all affected logger flows.
- No hardware acceptance, real-project rewrites, merge or deployment is implied.

Use existing Qt naming controls and a small Samples action. The dialog shows
composition/wire, local coverage, known records and explicit existing/new choices.

## Shared allocation options grounded in the current repository

The repository currently documents Google Drive exchange of raw runs and portable
reviews (`docs/current-annealing-review-folders.md` and
`docs/mini_dma_optimization_campaigns.md`). This is eventual file synchronization,
not a service that can atomically reserve the next sN across devices.

The existing automation endpoints do not supply that service: launcher session
control binds to 127.0.0.1, microscope control uses a user-local Qt socket, and the
power-supply broker defaults to localhost and owns instrument actions. Do not expose
or reuse those control endpoints as a network sample allocator. No deployed shared
sample-allocation authority was verified by this repository inspection.

Practical choices, in order of setup cost:

1. **One allocation owner/workstation.** Register new pieces there and transfer
   explicit registry snapshots before remeasurement elsewhere. This can be used
   with today's code, but only becomes an authoritative workflow once the owner is
   agreed and other computers stop independently allocating labels.
2. **Preassigned ranges per device/operator.** A designated owner reserves disjoint
   ranges for each source wire. Supports offline cutting, but creates gaps and
   needs explicit range enforcement and durable reservation records. The current
   UI does not enforce such ranges; do not claim this is implemented.
3. **Dedicated authenticated allocation service.** One transactional database
   enforces both UUID uniqueness and (composition, wire, label) uniqueness. An
   idempotency key makes retried requests return the original reservation. Atomic
   transactions allocate numbers; audit history records provenance; regular backups
   preserve unrecycled numbers. Clients may select cached existing pieces offline,
   but new authoritative allocation fails closed without the service unless a
   pre-reserved range is used. A LAN host could use server-local SQLite behind the
   service; a hosted database is another option only after verifying an approved
   deployment target and account. Never synchronize an open database file.

The service option needs an agreed owner, availability expectations, deployment
host, authentication/authorization, backups, and an offline policy. No deployment,
credentials, firewall/network changes or hardware control were performed for this
assessment. The work here prepares additive identity handling, not a hidden new
infrastructure dependency.

## Additive Builder and EDA provenance

Recognized current-annealing/current-program metadata and TMA imports retain the
explicit physical_sample fields when they match the source composition and wire.
No UUID is inferred from a historical filename. Existing content hashes and source
wire keys keep their meaning. Optional dataclass fields preserve old project loads;
compact TMA tables retain a JSON-text per-source identity ledger for lazy restoration.
Conflicting or malformed claims must not be resolved by selecting the last value.

The assembled Builder database is still a source-wire summary: it can combine
measurements and manual outcomes from several pieces and families. Its per-source
identity ledger is additive provenance, not a claim that every column belongs to
one piece. The explicit source_wire_summary scope keeps these rows independent in
EDA aggregation. No historical project is rewritten to add these fields.

Where a measurement-level table has complete consistent UUID evidence, EDA can
aggregate repeated measurements of that UUID within the same source wire. Distinct
pieces, incomplete/malformed claims and mixed provenance stay separate. Historical
tables with no identity evidence retain existing wire-level behavior. This does not
retroactively establish piece identity for those tables or migrate all scientific
analyses to a new piece-level schema.

Rejected identity evidence remains in the original compact table. Rebuilding a new
compact table from records whose identities were all rejected currently drops that
rejection history and can return to legacy wire-level aggregation. It does not
create a known UUID. Retain the original table for provenance; a durable rejection
status across every rebuild remains a follow-up limitation.
