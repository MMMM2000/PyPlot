# Recovering stale table manifest descriptors

An October 2026 database import failed because `sections/assemble/table.json`
and `sections/microscope/table.json` had different sizes and SHA-256 checksums
from those listed in the manifest. The ordinary importer correctly rejected it.
The exact process that changed those bytes was not established.

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
