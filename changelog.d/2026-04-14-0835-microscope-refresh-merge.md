2026-04-14 08:35

- Microwire Data Builder microscope refreshes now merge newly scanned microscope files into the existing section state instead of replacing earlier rows when only a subset of images is refreshed.
- Saved microscope review flags, overrides, OCR cache entries, and previously known microscope rows are preserved across partial refreshes.
- Saved reviewed microscope diameters are reapplied immediately after refresh/apply operations, preventing previously reviewed `d`, `D`, and `d/D` values from appearing blank while new microscope files are merged.
- Partial microscope refreshes no longer let empty placeholder entries overwrite previously saved detections for untouched wires, so old OCR/image provenance is preserved when only new microscope files are processed.
- Microscope-only Builder/export rows once again keep `Microscope only` provenance instead of falling back to fabrication-only labels when no fabrication records exist.
- Assemble preview/export now falls back to stored annealing and microscope payloads even when a section's in-memory payload marker is missing, keeping hidden-end filtering and saved measurement data available.
