# Kosice Origin Manual Stress-Strain Extraction

This workflow prototypes extraction of manual stress-strain data from the Kosice
Origin project into CSV files plus a manifest that can later feed the Microwire
Data Builder Manual stress/strain section.

The extractor never writes to the source `.opju`. It copies the project into
`artifacts/kosice_origin_extract/`, opens that copy read-only through
Origin automation, exports each worksheet to CSV, and writes `manifest.json`.

## Command

From the PyPlot repository on a Windows machine with Origin installed and
licensed:

```powershell
$env:TEMP = "$PWD\artifacts\tool-temp"
$env:TMP = $env:TEMP
$env:UV_CACHE_DIR = "$PWD\artifacts\uv-cache"
New-Item -ItemType Directory -Force -Path $env:TEMP, $env:UV_CACHE_DIR | Out-Null
uv run python scripts/kosice_origin_extract.py
```

Use `--show-origin` while debugging Origin automation. Use `--dry-run` to write a
manifest documenting the source/copy path without starting Origin.

## Outputs

- `artifacts/kosice_origin_extract/Stress-Strain-Ni50Fe27Ga23-CuCo.opju`: copied
  working project.
- `artifacts/kosice_origin_extract/csv/*.csv`: one CSV per Origin worksheet.
- `artifacts/kosice_origin_extract/manifest.json`: normalized worksheet manifest.

Each worksheet manifest entry includes:

- `sample_key`
- `workbook` and `workbook_long_name`
- `sheet` and `sheet_long_name`
- `columns` with short name, long name, units, comments, and normalized name
- `row_count`
- `csv_path`
- `manual_column_map` when displacement/load/strain/stress columns are detected
- `candidate_manual_stress_strain`

## Builder Integration Follow-Up

The next Builder worker should consume `manifest.json`, choose candidate
worksheets with strain/stress columns, and convert each chosen CSV into the
existing manual logger shape:

```text
displacement_mm, load_g, strain_pct, stress_mpa
```

Builder provenance should keep the worksheet source visible in the Manual
stress/strain section, for example by preserving workbook/sheet/csv metadata in
section payloads. Public Excel export should continue to expose the unified
manual stress/strain values only, without Origin workbook provenance columns.
