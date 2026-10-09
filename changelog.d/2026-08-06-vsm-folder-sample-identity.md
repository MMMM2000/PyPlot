Changed
-------
- Use an explicitly selected VSM sample folder as the sample identity for files
  directly inside it, and prefer that valid identity when a temperature-scan
  header contains a missing or conflicting microwire name.
- Preserve historical microscope rows and merged measurement payloads during
  copied-project automation refreshes, preventing targeted imports from
  dropping older measurements or manual dimension values.
