# Recovering stale table manifest descriptors

An October 2026 database import failed because `sections/assemble/table.json`
and `sections/microscope/table.json` had different sizes and SHA-256 checksums
from those listed in the manifest. The ordinary importer correctly rejected it.
The October 6 correction of Ni50Fe27Ga23 wire 9/3 changed its outer diameter
from 23.9 to 71.8 micrometres and its core/outer ratio from 0.653 to 0.217 in
both tables, but left the October 5 manifest unchanged. The exact editing
command was not retrieved. A backup from before that correction matches the
previously validated October 5 candidate byte-for-byte.

For a package with intentional table edits, explicitly accept those edited table
bytes into a separate, strictly verified recovered copy:

```powershell
uv run python scripts/recover_builder_table_manifest.py source-copy.pydpj recovered.pydpj
```

This command never overwrites either file, changes no table or payload bytes,
updates only the table descriptors and revision/writer timestamps, rejects
non-table corruption and invalid table schemas, and verifies every entry before
publishing. Recovery accepts the edited tables; it cannot prove those edits were
scientifically correct or restore their previous values. Keep the original and
the recovery output for provenance. Import the recovered file using the logger's
Project Browse/Import controls. Normal import does not perform automatic recovery.

All future updates should use the Builder writer, which computes descriptors and
fully verifies the package before atomic publication. Direct ZIP table edits
without manifest updates produce unreadable projects.

## Safe future table corrections

On a disposable source copy, prepare a JSON object mapping section names to
replacement table objects, each with exactly `columns`, `rows` and `index`:

```powershell
uv run python scripts/update_builder_tables.py source-copy.pydpj candidate.pydpj corrected-tables.json
```

This uses the ordinary writer, preserves section state and deferred payloads,
regenerates descriptors and validates every entry before publishing a new copy.
It refuses in-place edits, invalid tables and existing destinations. Ordinary
imports still reject stale or damaged manifests.

## Publishing a repaired or updated shared database

First fully verify the candidate and open it in Builder and import its tables
in the logger using isolated settings/storage. Do not experiment on the shared
database. Then, with explicit authorization to replace it:

```powershell
uv run python scripts/publish_builder_project.py candidate.pydpj shared-latest.pydpj --backup backups/before-update.pydpj --expected-target-sha256 OLD_SHA256
```

The target must still have the exact expected hash, the candidate must have the
same project identity and a newer revision, and the backup must not exist. The
command shares Builder's save lock, stages on the target filesystem, verifies
every staged entry and table projection, checks the exact backup and rechecks
the old target before atomic replacement. It checks the final published hash
and emits a JSON receipt. Retain that receipt with the backup. An invalid
candidate is never accepted merely because its tables are readable.

These safeguards protect supported workflows; they cannot prevent an external
program from bypassing them and editing ZIP members directly.
