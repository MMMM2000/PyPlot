# KoÅ¡ice Origin Manual Stress/Strain Extraction

This prototype extracts manual stress/strain worksheets from the KoÅ¡ice Origin project into files that can later be imported by the Microwire Data Builder Manual stress/strain section.

Source project:

```text
G:\Shared drives\CharakterizÃ¡cia mikrodrÃ´tov\shape memory database\Kosice\Stress-Strain-Ni50Fe27Ga23-CuCo.opju
```

The extractor never writes to the source `.opju`. When the file is readable and `originpro` can open Origin, it copies the project under `artifacts/kosice_origin_extract/origin_copy/`, opens that copy read-only, enumerates workbook sheets, and exports worksheets that can be normalized to:

```text
displacement_mm, load_g, strain_pct, stress_mpa
```

Run:

```powershell
.\.venv\Scripts\python.exe scripts\kosice_origin_manual_stress_extract.py
```

Dry-run / access check:

```powershell
.\.venv\Scripts\python.exe scripts\kosice_origin_manual_stress_extract.py --dry-run
```

Outputs:

- `artifacts/kosice_origin_extract/kosice_origin_extract_manifest.json`
- `artifacts/kosice_origin_extract/normalized_csv/*.csv`
- `artifacts/kosice_origin_extract/builder_txt/*.txt`

The `builder_txt` files use the same two-header-row TXT shape as the existing Manual Stress/Strain Logger exports, so the current Builder parser can read them. The manifest records workbook/sheet names, detected sample key, source columns, units, row count, and output paths. Keep this provenance visible in Builder-side artifacts; public Excel exports should hide or unify the KoÅ¡ice/Origin source label later in the export presentation layer.

Current worker result:

- `originpro` was importable from the project virtual environment.
- The shared-drive `.opju` was intermittently readable from Python even though PowerShell `Test-Path` reported access denied.
- A local copy was created under `artifacts/kosice_origin_extract/origin_copy/`.
- Origin opened the copied project when the path was passed as an absolute path.
- Numeric extraction is still blocked by Origin automation raising `SystemError: <built-in function ApplicationBase_LT_execute> returned a result with an exception set` while enumerating/extracting workbook data.

The current branch is therefore the Builder-facing extraction foundation: canonical normalization, Builder-ready TXT writing, manifest shape, dry-run/status reporting, and documented run commands. The next Origin-specific follow-up should focus only on the Origin worksheet data access method, possibly by using a different Origin API path, graph data range extraction, or manual worksheet export from the Origin UI into the normalized CSV/TXT contract above.
