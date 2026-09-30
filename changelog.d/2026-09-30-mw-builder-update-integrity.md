## Microwire Data Builder update integrity

- Load large packaged measurement collections record by record while retaining codec, blob integrity and project budget limits. Stop automation on invalid modern payloads.
- Preserve saved graph metadata and treatment variants across previews and project round trips.
- Retain embedded curves when refreshed sources cannot be parsed, keep VSM table grouping, and merge Windows source paths case-insensitively. Explicit graph pruning remains available; TMA keeps its active-run gating.
- Merge partial Fabrication updates into both indexes and retain saved values absent in new spreadsheets. Video updates restore project Fabrication data and retain manual readings.
- Recalculate video intervals after length overrides, retain fractional metres, and leave impossible intervals or missing preceding pieces unknown.
- Guard database promotion against concurrent saves, restore the previous project after reported promotion failures, and isolate each run's temporary folders. Correct the Praha TMA template and update instructions.
