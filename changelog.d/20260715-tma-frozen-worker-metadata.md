2026-07-15 (UTC)

- Freeze TMA session, recipe, hardware, operator, output, and timing metadata on the GUI thread before control workers start, preventing periodic metadata, scheduled logging, trace, setup, and fault paths from reading Qt widgets while preserving approved runtime-edit metadata.
