2026-04-14 08:35

- Microwire Data Builder microscope refreshes now merge newly scanned microscope files into the existing section state instead of replacing earlier rows when only a subset of images is refreshed.
- Saved microscope review flags, overrides, OCR cache entries, and previously known microscope rows are preserved across partial refreshes.
- Saved reviewed microscope diameters are reapplied immediately after refresh/apply operations, preventing previously reviewed `d`, `D`, and `d/D` values from appearing blank while new microscope files are merged.
