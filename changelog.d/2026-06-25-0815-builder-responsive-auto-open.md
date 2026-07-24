2026-06-25 08:15

- Made Microwire Data Builder startup project auto-open prepare large `.pydpj` files on a background Qt worker, including JSON parsing and embedded payload decoding, then restore GUI sections in staged event-loop turns.
- Added project-load preparation timing fields for read, JSON decode, payload decode, byte count, and decoded payload count to help diagnose future startup stalls.
