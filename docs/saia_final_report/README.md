# SAIA final report workspace

This branch keeps the final-report process reproducible without committing the
personal report itself or modifying the original OneDrive documents.

## Working model

- The official SAIA outline is the structural authority.
- The existing half-year report is the filled visual and content baseline.
- Draft DOCX files, copied source documents, renders, and extracted evidence
  stay under ignored `artifacts/saia_final_report/`.
- Reusable scripts and source-selection decisions are versioned here.
- Raw experimental data remains read-only in its existing storage locations.

## Build the baseline draft

The baseline builder makes a package-preserving copy of the half-year report
and changes only its first title paragraph from the half-year title to the
final-report title.

```powershell
.\.venv\Scripts\python.exe scripts\saia_final_report\build_baseline.py `
  artifacts\saia_final_report\template\half_year_report.docx `
  artifacts\saia_final_report\draft\saia_final_report_v0.docx `
  --expected-sha256 2f681edac19176c898dacae6ba89bf81dce9ffe46a7e9f91fd7971dcefbe254e
```

The builder fails if the retained source hash changes, if the expected title
is not found, or if any DOCX package part other than `word/document.xml`
changes.

## Planned content passes

1. Retain and shorten the common background and first-half results.
2. Replace the planned-work section with completed second-half work.
3. Add TMA as the main new result block.
4. Add synchronized thermal-camera and vacuum/air comparisons.
5. Add the expanded VSM study and the completed AC-susceptibility result.
6. Add the cross-method summary table and concrete dissemination outputs.
7. Complete administrative answers, date, signature slot, and final QA.

## Reporting rules

- Do not count archives, tests, automation-history copies, or very short runs
  as independent scientific measurements.
- Label MLX90640 values as apparent temperature.
- Label one-coil AC results as apparent susceptibility.
- Treat vacuum/air resistance differences as preliminary paired evidence.
- Do not publish automatic VSM transition candidates until manually reviewed.
- Use TMA for screening/protocol development and commercial DMA as the
  high-precision reference measurement.

## QA

The retained source files must remain byte-for-byte unchanged. Every meaningful
DOCX edit batch should end with structural audits and a rendered visual review.
The bundled renderer currently cannot run on this machine because LibreOffice
is not installed; use Word PDF export for the visual gate until LibreOffice is
available.
