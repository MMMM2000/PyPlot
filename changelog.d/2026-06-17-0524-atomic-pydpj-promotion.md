2026-06-17 05:24

- Made Microwire Data Builder automation write `.pydpj` and manifest JSON files through same-directory temporary files before replacing the destination.
- Made database latest promotion keep the previous `microwire_database_latest.pydpj` in place until the new latest project copy is durable, then archive the previous latest files.
